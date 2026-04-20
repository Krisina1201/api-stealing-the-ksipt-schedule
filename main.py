# main.py
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from fastapi import FastAPI, HTTPException, Query, status
import uvicorn
from datetime import date
import json
from pydantic import BaseModel
from typing import List, Optional, Any
import psycopg2
from psycopg2.extras import RealDictCursor
from pgdbm import DatabaseConfig
import logging
from fastapi.middleware.cors import CORSMiddleware

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создаем ОДИН экземпляр FastAPI
app = FastAPI(
    title="КСИПТ API",
    description="API для работы с расписанием, инвентарем и ответственными лицами",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "*"  # Для тестирования, потом можно убрать
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== МОДЕЛИ ДАННЫХ ==========

@dataclass
class Lesson:
    day: str
    date: str
    lesson_number: str
    time: Optional[str]
    discipline: str
    location: str
    classroom: str
    teacher: str
    group: str


class InventoryItem(BaseModel):
    """Модель для предмета инвентаря"""
    inventory_number: Optional[int] = None
    item_name: Optional[str] = None
    manufacturer: Optional[str] = None
    model: Optional[str] = None
    condition_description: Optional[str] = None
    warranty_until: Optional[str] = None
    notes: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    inventory_type_title: Optional[str] = None


class ResponsiblePerson(BaseModel):
    """Модель для ответственного лица"""
    id: Optional[int] = None
    last_name: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    position: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[str] = None
    classroom_id: Optional[int] = None


# ========== КОНФИГУРАЦИЯ ==========

config = DatabaseConfig(
    host="localhost",
    port=5432,
    database="FankyPop",
    user="admin",
    password="123"
)

# ========== ФУНКЦИИ ДЛЯ РАСПИСАНИЯ ==========

groups_cache = None


def fetch_groups(force_refresh: bool = False) -> Dict[str, str]:
    global groups_cache

    if groups_cache is not None and not force_refresh:
        return groups_cache

    url = "https://e-spo.ru/org/export/rasp?pid=1"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    response = requests.get(url, headers=headers)
    response.encoding = 'utf-8'

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail="Не удалось загрузить список групп")

    soup = BeautifulSoup(response.text, 'html.parser')
    select = soup.find('select', {'id': 'raspbasesearch-group_id'})

    if not select:
        raise HTTPException(status_code=500, detail="Не найден выпадающий список с группами")

    groups = {}
    for option in select.find_all('option'):
        group_id = option.get('value')
        group_name = option.text.strip()
        if group_id and group_name and group_id.isdigit():
            groups[group_name] = group_id

    groups_cache = groups
    return groups


def parse_schedule_for_group(group_id: str, group_name: str = "") -> List[Lesson]:
    url = f"https://e-spo.ru/org/export/rasp?pid=1&RaspBaseSearch%5Bgroup_id%5D={group_id}"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    response = requests.get(url, headers=headers)
    response.encoding = 'utf-8'

    if response.status_code != 200:
        raise HTTPException(status_code=500, detail=f"Ошибка загрузки расписания: {response.status_code}")

    soup = BeautifulSoup(response.text, 'html.parser')
    days_cards = soup.find_all('div', class_='card')

    all_lessons = []

    for card in days_cards:
        header = card.find('div', class_='card-header')
        if not header:
            continue

        header_text = header.get_text(strip=True)
        if ' - ' in header_text:
            day_name, date_str = header_text.split(' - ', 1)
        else:
            day_name, date_str = header_text, ""

        table = card.find('table')
        if not table:
            continue

        rows = table.find('tbody').find_all('tr') if table.find('tbody') else table.find_all('tr')[1:]

        for row in rows:
            cells = row.find_all('td')
            if len(cells) < 4:
                continue

            lesson_number_cell = cells[0]
            lesson_number = lesson_number_cell.get_text(strip=True).split('\n')[0]
            time_tag = lesson_number_cell.find('p')
            time_str = time_tag.get_text(strip=True) if time_tag else ""

            discipline_cell = cells[1]
            discipline_parts = discipline_cell.get_text('\n', strip=True).split('\n')
            discipline_name = discipline_parts[0] if discipline_parts else ""

            location = "наб.реки Смол., 1"
            small_tag = discipline_cell.find('small')
            if small_tag:
                location = small_tag.get_text(strip=True)

            classroom_cell = cells[2]
            classroom = classroom_cell.get_text(strip=True)

            teacher_cell = cells[3]
            teacher = teacher_cell.get_text(strip=True)

            lesson = Lesson(
                day=day_name,
                date=date_str,
                lesson_number=lesson_number,
                time=time_str,
                discipline=discipline_name,
                location=location,
                classroom=classroom,
                teacher=teacher,
                group=group_name or f"ID:{group_id}"
            )

            if discipline_name and discipline_name != "None":
                all_lessons.append(lesson)

    return all_lessons


# ========== ФУНКЦИИ ДЛЯ БАЗЫ ДАННЫХ ==========

def get_db_connection():
    """Создает подключение к базе данных"""
    try:
        conn_params = {
            'host': config.host,
            'port': config.port,
            'database': config.database,
            'user': config.user,
            'password': config.password
        }
        conn = psycopg2.connect(**conn_params)
        conn.autocommit = False
        return conn
    except psycopg2.Error as e:
        logger.error(f"Ошибка подключения к БД: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ошибка подключения к базе данных"
        )


# ========== ЭНДПОИНТЫ РАСПИСАНИЯ ==========

@app.get("/")
async def root():
    return {
        "message": "КСИПТ API",
        "endpoints": {
            "Расписание": {
                "/groups": "Список всех групп",
                "/schedule/group/{group_name}": "Расписание для конкретной группы",
                "/schedule/group-id/{group_id}": "Расписание по ID группы",
                "/search": "Поиск по группе или преподавателю"
            },
            "Инвентарь и ответственные": {
                "/inventory/{classroom_id}": "Получить инвентарь по ID аудитории",
                "/responsible/{classroom_id}": "Получить ответственных лиц по ID аудитории",
                "/classroom/{classroom_id}/full": "Полная информация об аудитории"
            },
            "Документация": {
                "/docs": "Swagger UI",
                "/redoc": "ReDoc"
            }
        }
    }


@app.get("/groups")
async def get_groups(force_refresh: bool = False):
    try:
        groups = fetch_groups(force_refresh)
        return {"total": len(groups), "groups": groups}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


from datetime import date
from datetime import datetime


@app.get("/schedule/classroom/{room_number}")
async def get_schedule_by_classroom(
        room_number: str,
        force_refresh_groups: bool = False
):
    try:
        # находим все группы
        groups_all = fetch_groups(force_refresh_groups)
        groups_items = list(groups_all.items())[11:]  # список кортежей (ключ, значение)
        groups = dict(groups_items)
        today = date.today()

        #переменная для подходящих значений
        classroom_schedule = []
        errors = []

        #так как в переменной с группами лежат два значения, перебираем их оба
        for group_name, group_id in groups.items():
            try:
                lessons = parse_schedule_for_group(group_id, group_name)

                for lesson in lessons:
                    lesson_date_obj = datetime.strptime(lesson.date, '%d.%m.%Y').date()
                    if lesson_date_obj != today:
                        continue

                    if (room_number == lesson.classroom):
                        lesson_data = {
                            "group": group_name,
                            "group_id": group_id,
                            "classroom": lesson.classroom,
                            "time": getattr(lesson, 'time', 'Время не указано'),
                            "subject": lesson.discipline, #getattr(lesson, 'subject', 'Предмет не указан'),
                            "teacher": getattr(lesson, 'teacher', 'Преподаватель не указан'),
                        }

                        classroom_schedule.append(lesson_data)
        #
            except Exception as e:
                errors.append(f"Ошибка при обработке группы {group_name}: {str(e)}")
        #
        classroom_schedule.sort(key=lambda x: x.get('time', ''))

        if not classroom_schedule:
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"На сегодня ({today}) не найдено занятий в кабинете {room_number}",
                    "room": room_number,
                    "date": today.isoformat(),
                    "possible_reasons": [
                        "Проверьте правильность номера кабинета",
                        "В кабинете могут быть занятия в другой день",
                        "Возможно, поле с кабинетом называется иначе",
                        f"Всего проверено групп: {len(groups)}"
                    ],
                    "debug_errors": errors if errors else None
                }
            )

        return {
            "room": room_number,
            "date": today.isoformat(),
            "total_lessons": len(classroom_schedule),
            "schedule": classroom_schedule
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/schedule/classrooms/all")
async def get_all_classrooms(
        force_refresh_groups: bool = False
):
    """
    Получить список всех уникальных кабинетов из расписания
    """
    try:
        groups = fetch_groups(force_refresh_groups)

        all_classrooms = set()
        classrooms_details = {}

        for group_name, group_id in groups.items():
            lessons = parse_schedule_for_group(group_id, group_name)

            for lesson in lessons:
                # Определяем поле с кабинетом
                classroom = None
                if hasattr(lesson, 'classroom'):
                    classroom = lesson.classroom
                elif hasattr(lesson, 'room'):
                    classroom = lesson.room
                elif hasattr(lesson, 'auditorium'):
                    classroom = lesson.auditorium
                elif hasattr(lesson, 'cabinet'):
                    classroom = lesson.cabinet

                if classroom and str(classroom).strip():
                    classroom_str = str(classroom).strip()
                    all_classrooms.add(classroom_str)

                    # Собираем дополнительную информацию
                    if classroom_str not in classrooms_details:
                        classrooms_details[classroom_str] = {
                            "count": 0,
                            "groups": set()
                        }
                    classrooms_details[classroom_str]["count"] += 1
                    classrooms_details[classroom_str]["groups"].add(group_name)

        # Сортируем кабинеты
        sorted_classrooms = sorted(all_classrooms)

        # Преобразуем множества в списки для JSON
        for classroom in classrooms_details:
            classrooms_details[classroom]["groups"] = list(classrooms_details[classroom]["groups"])

        return {
            "total_classrooms": len(sorted_classrooms),
            "classrooms": sorted_classrooms,
            "details": classrooms_details
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



# ========== ЭНДПОИНТЫ ИНВЕНТАРЯ И ОТВЕТСТВЕННЫХ ==========




@app.get("/inventory/{classroom_name}", response_model=List[InventoryItem])
async def get_inventory_by_classroom(classroom_name: str):
    """
    Получить инвентарь для указанной аудитории

    - **classroom_name**: Номер аудитории (например: 101, 3.35, А-408)
    """
    conn = None
    try:
        conn = get_db_connection()

        # Получаем ID аудитории по ее номеру
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM get_classroom_func2(%s)", (classroom_name,))
            result = cur.fetchone()

            if not result or 'id' not in result:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Аудитория '{classroom_name}' не найдена"
                )

            classroom_id = result['id']

        # Получаем инвентарь для найденной аудитории
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM get_inventory_type_func(%s)", (classroom_id,))
            results = cur.fetchall()

            if not results:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Инвентарь для аудитории {classroom_name} (ID: {classroom_id}) не найден"
                )

            # Преобразуем результаты в список объектов InventoryItem
            items = [
                InventoryItem(
                    inventory_number=row.get("inventory_number"),
                    item_name=row.get("item_name"),
                    manufacturer=row.get("manufacturer"),
                    model=row.get("model"),
                    condition_description=row.get("condition_description"),
                    warranty_until=str(row.get("warranty_until")) if row.get("warranty_until") else None,
                    notes=row.get("notes"),
                    created_at=str(row.get("created_at")) if row.get("created_at") else None,
                    updated_at=str(row.get("updated_at")) if row.get("updated_at") else None,
                    inventory_type_title=row.get("inventory_type_title")
                )
                for row in results
            ]

            return items

    except HTTPException:
        raise
    except psycopg2.Error as e:
        logger.error(f"Ошибка БД: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка базы данных: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Внутренняя ошибка сервера: {str(e)}"
        )
    finally:
        if conn and not conn.closed:
            conn.close()


@app.get("/responsible/{classroom_id}", response_model=List[ResponsiblePerson])
async def get_responsible_people(classroom_id: int):
    """
    Получить ответственных лиц для указанной аудитории

    - **classroom_id**: ID аудитории
    """
    conn = None
    try:
        conn = get_db_connection()

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM get_responsible_person_func(%s)",
                (classroom_id,)
            )
            result = cur.fetchall()

            if not result:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Ответственные лица для аудитории {classroom_id} не найдены"
                )

            conn.commit()
            return result

    except HTTPException:
        raise
    except psycopg2.Error as e:
        logger.error(f"Ошибка БД: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка базы данных: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Внутренняя ошибка сервера: {str(e)}"
        )
    finally:
        if conn and not conn.closed:
            conn.close()


@app.get("/classroom/{classroom_id}/full")
async def get_classroom_full_info(classroom_id: int):
    """
    Получить полную информацию об аудитории (инвентарь и ответственные лица)

    - **classroom_id**: ID аудитории
    """
    conn = None
    try:
        conn = get_db_connection()

        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM get_inventory_type_func(%s)",
                (classroom_id,)
            )
            inventory = cur.fetchall()

            cur.execute(
                "SELECT * FROM get_responsible_person_func(%s)",
                (classroom_id,)
            )
            responsible = cur.fetchall()

            conn.commit()

            return {
                "classroom_id": classroom_id,
                "inventory": inventory if inventory else [],
                "responsible_people": responsible if responsible else []
            }

    except psycopg2.Error as e:
        logger.error(f"Ошибка БД: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Ошибка базы данных: {str(e)}"
        )
    except Exception as e:
        logger.error(f"Неожиданная ошибка: {e}")
        if conn:
            conn.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Внутренняя ошибка сервера: {str(e)}"
        )
    finally:
        if conn and not conn.closed:
            conn.close()


@app.get("/health")
async def health_check():
    """Проверка работоспособности сервиса"""
    try:
        conn = get_db_connection()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        conn.close()
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Service unavailable"
        )


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)