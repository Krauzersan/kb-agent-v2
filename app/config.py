"""Инфраструктурная конфигурация (только из окружения).

ВАЖНО: API-ключи здесь НЕ хранятся. Ключи Claude и Пачки вводятся в админ-панели
(вкладка «Настройки») и лежат в базе — см. settings_store.py.
Здесь только то, что нужно для самого запуска приложения.
"""
import os


class Settings:
    # Порт веб-сервиса (заведомо нестандартный, чтобы не конфликтовать)
    PORT: int = int(os.getenv("PORT", "8745"))
    HOST: str = os.getenv("HOST", "0.0.0.0")

    # Где хранить данные: файлы БЗ, SQLite, встроенный Qdrant, кэш модели
    DATA_DIR: str = os.getenv("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))

    # Модель эмбеддингов (меняется только перезапуском, не в UI)
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "intfloat/multilingual-e5-base")
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "knowledge_base")
    # Настоящий Qdrant-сервер (быстрый). Локально, наружу не выставляется.
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://127.0.0.1:6333")

    # Нарезка текста
    CHUNK_SIZE: int = int(os.getenv("CHUNK_SIZE", "900"))
    CHUNK_OVERLAP: int = int(os.getenv("CHUNK_OVERLAP", "150"))

    # Админ-панель (это НЕ api-ключи — нужны для самого входа, поэтому в env)
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin")
    SESSION_SECRET: str = os.getenv("SESSION_SECRET", "change-me")
    # Имя сессионной куки. "session" — дефолт Starlette. Если на одном хосте крутится
    # несколько копий сервиса (разные порты) — им нужны РАЗНЫЕ имена куки, иначе кука
    # общая на весь домен (порт в ней не участвует) и вход в одну копию разлогинивает
    # другую (кука перезаписывается чужим secret'ом, который не расшифровать).
    SESSION_COOKIE_NAME: str = os.getenv("SESSION_COOKIE_NAME", "session")
    # Ключ шифрования секретов в settings_store (API-ключи, токены) — отдельный от
    # SESSION_SECRET намеренно (разные назначения, компрометация одного не должна
    # автоматически компрометировать другой). Пусто = секреты хранятся как раньше,
    # без шифрования (совместимость со старыми деплоями без этой переменной).
    ENCRYPTION_KEY: str = os.getenv("ENCRYPTION_KEY", "")

    @property
    def DATA_DIR_ABS(self) -> str:
        return os.path.abspath(self.DATA_DIR)

    @property
    def KB_DIR(self) -> str:
        return os.path.join(self.DATA_DIR_ABS, "knowledge_base")

    @property
    def ASSETS_DIR(self) -> str:
        # Публично раздаваемые картинки (из ZIP-импорта) — НЕ весь KB_DIR,
        # чтобы не открывать наружу исходные тексты базы знаний.
        return os.path.join(self.DATA_DIR_ABS, "kb_assets")

    @property
    def DB_PATH(self) -> str:
        return os.path.join(self.DATA_DIR_ABS, "app.db")

    @property
    def QDRANT_PATH(self) -> str:
        # Встроенный Qdrant: данные лежат файлами на диске, отдельный сервис не нужен
        return os.path.join(self.DATA_DIR_ABS, "qdrant_local")

    @property
    def HF_HOME(self) -> str:
        return os.path.join(self.DATA_DIR_ABS, "hf_cache")


settings = Settings()

# Кэш моделей HuggingFace — внутри data, чтобы не качать модель повторно
os.environ.setdefault("HF_HOME", settings.HF_HOME)
