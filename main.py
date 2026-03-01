# main.py
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
from dataclasses import dataclass, asdict
from fastapi import FastAPI, HTTPException, Query
import uvicorn
from datetime import date
import json


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


app = FastAPI(
    title="API Расписания КСИПТ",
    description="API для получения расписания занятий колледжа",
    version="1.0.0"
)


@app.get("/")
async def root():
    return {
        "message": "API Расписания КСИПТ",
        "endpoints": {
            "/groups": "Список всех групп",
            "/schedule/group/{group_name}": "Расписание для конкретной группы",
            "/schedule/group-id/{group_id}": "Расписание по ID группы",
            "/search": "Поиск по группе или преподавателю",
            "/docs": "Документация Swagger"
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


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)