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

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Создаем ОДИН экземпляр FastAPI
app = FastAPI(
    title="КСИПТ API",
    description="API для работы с расписанием, инвентарем и ответственными лицами",
    version="1.0.0"
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
    inventory_id: Optional[int] = None
    classroom_id: Optional[int] = None
    item_type: Optional[int] = None
    item_name: Optional[str] = None


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


@app.get("/schedule/group/{group_name}")
async def get_schedule_by_group_name(
        group_name: str,
        force_refresh_groups: bool = False
):
    try:
        groups = fetch_groups(force_refresh_groups)

        if group_name not in groups:
            similar = [g for g in groups.keys() if group_name.lower() in g.lower()]
            raise HTTPException(
                status_code=404,
                detail={
                    "message": f"Группа '{group_name}' не найдена",
                    "similar_groups": similar[:5]
                }
            )

        group_id = groups[group_name]
        lessons = parse_schedule_for_group(group_id, group_name)

        return {
            "group": group_name,
            "group_id": group_id,
            "total_lessons": len(lessons),
            "schedule": [asdict(lesson) for lesson in lessons]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/schedule/group-id/{group_id}")
async def get_schedule_by_group_id(group_id: str):
    try:
        lessons = parse_schedule_for_group(group_id, f"ID:{group_id}")
        return {
            "group_id": group_id,
            "total_lessons": len(lessons),
            "schedule": [asdict(lesson) for lesson in lessons]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/search")
async def search_schedule(
        group: Optional[str] = None,
        teacher: Optional[str] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None
):
    try:
        groups = fetch_groups()

        if not group and not teacher:
            raise HTTPException(
                status_code=400,
                detail="Укажите хотя бы один параметр поиска (group или teacher)"
            )

        all_lessons = []

        if group:
            if group in groups:
                lessons = parse_schedule_for_group(groups[group], group)
                all_lessons.extend(lessons)
            else:
                raise HTTPException(status_code=404, detail=f"Группа '{group}' не найдена")

        if date_from or date_to:
            filtered = []
            for lesson in all_lessons:
                if date_from and lesson.date < date_from:
                    continue
                if date_to and lesson.date > date_to:
                    continue
                filtered.append(lesson)
            all_lessons = filtered

        if teacher:
            filtered = []
            for lesson in all_lessons:
                if teacher.lower() in lesson.teacher.lower():
                    filtered.append(lesson)
            all_lessons = filtered

        return {
            "search_params": {
                "group": group,
                "teacher": teacher,
                "date_from": date_from,
                "date_to": date_to
            },
            "total_found": len(all_lessons),
            "results": [asdict(lesson) for lesson in all_lessons]
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ========== ЭНДПОИНТЫ ИНВЕНТАРЯ И ОТВЕТСТВЕННЫХ ==========

@app.get("/inventory/{classroom_id}", response_model=List[InventoryItem])
async def get_inventory_by_classroom(classroom_id: int):
    """
    Получить инвентарь для указанной аудитории

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
            result = cur.fetchall()

            if not result:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Инвентарь для аудитории {classroom_id} не найден"
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