import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import SESSION_HISTORY_LIMIT, SESSIONS_DIR


class SessionStore:
    def __init__(self, sessions_dir: Path = SESSIONS_DIR, history_limit: int = SESSION_HISTORY_LIMIT):
        self.sessions_dir = sessions_dir
        self.history_limit = history_limit
        self.sessions_dir.mkdir(parents=True, exist_ok=True)

    def load(self, chat_id: int | str) -> dict[str, Any]:
        path = self._path(chat_id)
        if not path.exists():
            return self._new_session(chat_id)

        try:
            data = json.loads(path.read_text(encoding="utf-8-sig"))
        except (json.JSONDecodeError, OSError):
            data = self._new_session(chat_id)

        data.setdefault("chat_id", str(chat_id))
        data.setdefault("name", None)
        data.setdefault("city", None)
        data.setdefault("onboarding_stage", "new")
        data.setdefault("history", [])
        data["history"] = data["history"][-self.history_limit :]
        return data

    def save(self, session: dict[str, Any]) -> None:
        path = self._path(session["chat_id"])
        temp_path = path.with_suffix(".tmp")
        session["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        text = json.dumps(session, ensure_ascii=False, indent=2)
        temp_path.write_text(text, encoding="utf-8")
        temp_path.replace(path)

    def append_message(self, session: dict[str, Any], role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError(f"Unsupported role: {role}")
        session.setdefault("history", []).append(
            {
                "role": role,
                "content": content,
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )
        session["history"] = session["history"][-self.history_limit :]

    def set_name(self, session: dict[str, Any], raw_name: str) -> str:
        name = self._clean_name(raw_name)
        session["name"] = name
        return name

    def set_city(self, session: dict[str, Any], raw_city: str) -> str:
        city = self._clean_city(raw_city)
        session["city"] = city
        return city

    def llm_history(self, session: dict[str, Any]) -> list[dict[str, str]]:
        history = [
            {"role": item["role"], "content": item["content"]}
            for item in session.get("history", [])[-self.history_limit :]
            if item.get("role") in {"user", "assistant"} and item.get("content")
        ]
        profile_parts = []
        if session.get("name"):
            profile_parts.append(f"клиента зовут {session['name']}")
        if session.get("city"):
            profile_parts.append(f"город клиента: {session['city']}")
        if profile_parts:
            return [{"role": "system", "content": "Постоянный контекст: " + "; ".join(profile_parts) + "."}] + history
        return history

    def _path(self, chat_id: int | str) -> Path:
        safe_chat_id = re.sub(r"[^0-9A-Za-z_-]", "_", str(chat_id))
        return self.sessions_dir / f"{safe_chat_id}.txt"

    def _new_session(self, chat_id: int | str) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return {
            "chat_id": str(chat_id),
            "name": None,
            "city": None,
            "onboarding_stage": "new",
            "history": [],
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _clean_name(raw_name: str) -> str:
        name = re.sub(r"\s+", " ", raw_name).strip()
        name = re.sub(r"^(меня зовут|зовут|я)\s+", "", name, flags=re.I).strip()
        return name[:80] or "клиент"

    @staticmethod
    def _clean_city(raw_city: str) -> str:
        city = re.sub(r"\s+", " ", raw_city).strip(" .,!?:;")
        city = re.sub(r"^(город|г\.?)\s+", "", city, flags=re.I).strip()
        return city[:80] or "Алматы"
