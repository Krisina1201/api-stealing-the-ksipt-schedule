from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from typing import List, Optional, Any
import psycopg2
from psycopg2.extras import RealDictCursor
from pgdbm import DatabaseConfig
import logging

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Конфигурация базы данных
config = DatabaseConfig(
    host="localhost",
    port=5432,
    database="FankyPop",
    user="admin",
    password="123"
)

app = FastAPI(
    title="FankyPop API",
    description="API для работы с инвентарем и ответственными лицами",
    version="1.0.0"
)


# Pydantic модели для ответов
class InventoryItem(BaseModel):
    """Модель для предмета инвентаря"""
    # Добавьте реальные поля из вашей таблицы inventory
    inventory_id: Optional[int] = None
    classroom_id: Optional[int] = None
    item_type: Optional[int] = None
    item_name: Optional[str] = None
    # Добавьте другие поля по необходимости


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


# Вспомогательная функция для подключения к БД
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
            # Вызываем функцию get_inventory_type_func
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
            # Вызываем функцию get_responsible_person_func
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


# Дополнительный эндпоинт для поиска по обоим параметрам
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
            # Получаем инвентарь
            cur.execute(
                "SELECT * FROM get_inventory_type_func(%s)",
                (classroom_id,)
            )
            inventory = cur.fetchall()

            # Получаем ответственных лиц
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


# Эндпоинт для проверки здоровья сервиса
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