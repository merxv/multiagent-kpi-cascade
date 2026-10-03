"""Слой-абстракция над LLM.

Провайдер выбирается в .env: LLM_PROVIDER=anthropic|openai|ollama|mock.
Все провайдеры реализуют один метод complete(system, user, agent) -> LLMResponse,
поэтому агенты не знают, какая модель стоит за клиентом.

Повторные попытки с экспоненциальной паузой (tenacity) делает LLMClient.
"""
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from tenacity import (Retrying, retry_if_not_exception_type, stop_after_attempt,
                      wait_exponential)

MOCK_DIR = Path(__file__).resolve().parent / "mock_responses"


class LLMError(Exception):
    """LLM не ответил после всех повторных попыток."""


class LLMConfigError(LLMError):
    """Ошибка настройки (нет ключа, нет пакета, нет заготовки). Повторять бессмысленно."""


@dataclass
class LLMResponse:
    text: str
    model: str
    tokens_in: int | None = None
    tokens_out: int | None = None


# ---------------------------------------------------------------------------
# Провайдеры
# ---------------------------------------------------------------------------
class MockProvider:
    """Возвращает заранее заготовленные JSON-ответы — для тестов без интернета и затрат.

    Заготовки лежат в llm/mock_responses/<агент>/*.json в виде
    {"match": ["ключевое слово", ...], "response": {...}}.
    Выбирается заготовка, все ключевые слова которой встречаются в запросе;
    если подходят несколько — та, у которой ключевых слов больше (самая конкретная).
    """
    name = "mock"

    def __init__(self, mock_dir: Path = MOCK_DIR):
        self.mock_dir = Path(mock_dir)
        self.model = "mock"

    def complete(self, system: str, user: str, agent: str, max_tokens: int) -> LLMResponse:
        folder = self.mock_dir / agent
        candidates = []
        for path in sorted(folder.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            keys = data.get("match", [])
            if all(k in user for k in keys):
                candidates.append((len(keys), path.name, data))
        if not candidates:
            raise LLMConfigError(f"Нет mock-ответа для агента '{agent}' в {folder}")
        _, _, best = max(candidates, key=lambda c: c[0])
        text = json.dumps(best["response"], ensure_ascii=False)
        return LLMResponse(text=text, model="mock")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str, timeout: float):
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise LLMConfigError("Не задан ANTHROPIC_API_KEY в файле .env")
        try:
            import anthropic
        except ImportError as e:
            raise LLMConfigError("Не установлен пакет anthropic: pip install anthropic") from e
        # max_retries=0: повторы делает tenacity в LLMClient, чтобы они попадали в лог
        self.client = anthropic.Anthropic(timeout=timeout, max_retries=0)
        self.model = model

    def complete(self, system: str, user: str, agent: str, max_tokens: int) -> LLMResponse:
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return LLMResponse(text=text, model=self.model,
                           tokens_in=resp.usage.input_tokens, tokens_out=resp.usage.output_tokens)


class OpenAIProvider:
    name = "openai"

    def __init__(self, model: str, timeout: float):
        if not os.getenv("OPENAI_API_KEY"):
            raise LLMConfigError("Не задан OPENAI_API_KEY в файле .env")
        try:
            import openai
        except ImportError as e:
            raise LLMConfigError("Не установлен пакет openai: pip install openai") from e
        self.client = openai.OpenAI(timeout=timeout, max_retries=0)
        self.model = model

    def complete(self, system: str, user: str, agent: str, max_tokens: int) -> LLMResponse:
        resp = self.client.chat.completions.create(
            model=self.model,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        usage = resp.usage
        return LLMResponse(text=resp.choices[0].message.content or "", model=self.model,
                           tokens_in=usage.prompt_tokens if usage else None,
                           tokens_out=usage.completion_tokens if usage else None)


class OllamaProvider:
    """Локальная модель через HTTP API Ollama (без дополнительных пакетов)."""
    name = "ollama"

    def __init__(self, model: str, url: str, timeout: float):
        self.model = model
        self.url = url.rstrip("/") + "/api/chat"
        self.timeout = timeout

    def complete(self, system: str, user: str, agent: str, max_tokens: int) -> LLMResponse:
        body = json.dumps({
            "model": self.model,
            "stream": False,
            "format": "json",
            "options": {"num_predict": max_tokens},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }).encode("utf-8")
        req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return LLMResponse(text=data["message"]["content"], model=self.model,
                           tokens_in=data.get("prompt_eval_count"), tokens_out=data.get("eval_count"))


def make_provider(settings):
    p = settings.llm_provider
    if p == "mock":
        return MockProvider()
    if p == "anthropic":
        return AnthropicProvider(settings.anthropic_model, settings.llm_timeout_sec)
    if p == "openai":
        return OpenAIProvider(settings.openai_model, settings.llm_timeout_sec)
    if p == "ollama":
        return OllamaProvider(settings.ollama_model, settings.ollama_url, settings.llm_timeout_sec)
    raise LLMConfigError(f"Неизвестный LLM_PROVIDER='{p}'. Допустимо: anthropic, openai, ollama, mock")


# ---------------------------------------------------------------------------
# Клиент с повторными попытками
# ---------------------------------------------------------------------------
class LLMClient:
    def __init__(self, settings, provider=None):
        self.settings = settings
        self._provider = provider  # можно подставить свой провайдер (например, в тестах)
        self._provider_error: LLMConfigError | None = None

    @property
    def provider(self):
        # Провайдер создаётся лениво: ошибка настройки (например, нет ключа) превратится
        # в понятное сообщение у первого агента, а не в падение при старте
        if self._provider is None and self._provider_error is None:
            try:
                self._provider = make_provider(self.settings)
            except LLMConfigError as e:
                self._provider_error = e
        if self._provider_error:
            raise self._provider_error
        return self._provider

    @property
    def model_name(self) -> str:
        try:
            return self.provider.model
        except LLMConfigError:
            return self.settings.llm_provider

    def complete(self, system: str, user: str, agent: str, on_retry=None) -> LLMResponse:
        """Вызов LLM с повторами при временных сбоях (сеть, таймаут, перегрузка)."""
        provider = self.provider

        def before_sleep(state):
            if on_retry:
                on_retry(state.attempt_number, state.outcome.exception())

        retryer = Retrying(
            stop=stop_after_attempt(max(1, self.settings.llm_max_retries)),
            wait=wait_exponential(multiplier=self.settings.retry_base_sec, max=20),
            retry=retry_if_not_exception_type(LLMConfigError),
            before_sleep=before_sleep,
            reraise=True,
        )
        try:
            return retryer(provider.complete, system, user, agent, self.settings.llm_max_tokens)
        except LLMConfigError:
            raise
        except Exception as e:
            raise LLMError(
                f"LLM ({provider.name}) не ответил после {self.settings.llm_max_retries} попыток: "
                f"{type(e).__name__}: {e}"
            ) from e
