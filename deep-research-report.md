# Исследование инструментов для проекта «ИИ-аватар деканата»
 охватывает локальный ASR, VAD/AEC, RAG и ingestion, embeddings/rerankers/vector DB, обе ветки генерации ответа, локальные LLM-серверы, TTS и voice cloning, MetaHuman/Audio2Face/MuseTalk, kiosk-shell, IPC, MCP, Codex Skills, тестирование, observability, privacy и deployment.

## Главное открытие по использованию Codex вместо API

В части требования преподавателя нашёлся существенно более нормальный вариант, чем автоматизировать веб-интерфейс ChatGPT/Codex.

OpenAI официально поддерживает **неинтерактивный `codex exec`**: Codex CLI можно вызывать из скрипта или другого приложения, передавать ему задачу и получать последнее сообщение агента через stdout. Более того, `codex exec` поддерживает JSONL-события и `--output-schema`, то есть финальный ответ можно принудительно формировать по JSON Schema. citeturn18search0turn18search4turn18search12

Это непосредственно позволяет построить ветку:

```text
распознанный вопрос
        ↓
локальный retrieval / Knowledge Base MCP
        ↓
codex exec
        ↓
структурированный JSON
        ↓
answer → TTS
sources → UI / журнал
        ↓
аватар
```

Codex CLI при этом умеет авторизоваться через **Sign in with ChatGPT**, а не только через API key. Следовательно, для такого режима **не требуется Selenium, Playwright или парсинг HTML страницы ChatGPT**. citeturn18search9

В документ я также включил второй официальный уровень интеграции — **Codex SDK**. На текущую дату OpenAI публикует stable Python SDK `openai-codex`; он управляет локальным Codex runtime и предназначен для встраивания Codex в приложение. citeturn18search1

При этом есть важное различие:

| Механизм | Для чего |
|---|---|
| `codex exec` | Самый простой программный вызов из backend-процесса |
| Codex SDK | Более глубокая программная интеграция |
| Codex App Server напрямую | Низкоуровневый JSON-RPC; прямой app-server command пока experimental |
| `codex mcp-server` | **Уже удалён, использовать нельзя** |

OpenAI удалил прежний `codex mcp-server` после его deprecation 24 августа 2026 года и теперь направляет интеграции к новому app-server/SDK стеку. При этом сама документация предупреждает, что прямой app-server command пока не поддерживается как production workload. citeturn18search5turn18search16

Есть и существенная оговорка к формулировке «не тратить токены по API». Вход через ChatGPT действительно позволяет работать **без отдельного API-ключа и отдельного API billing**, но Codex всё равно расходует allowance/credits ChatGPT-плана. OpenAI прямо пишет, что Codex activity подчиняется plan usage limits, а после исчерпания включённого объёма на поддерживаемых планах могут использоваться дополнительные кредиты. Поэтому такой канал нельзя считать бесплатным или безлимитным. citeturn18search2turn18search6

Также OpenAI уже имеет более общий механизм использования ChatGPT-плана в поддерживаемых сторонних приложениях: пользователь может разрешить eligible AI requests учитывать в лимите своего ChatGPT Work/Codex allowance без передачи приложению API key. Однако это не означает автоматически, что произвольное самописное kiosk-приложение уже может использовать данный механизм, поэтому в исследовании я не подменял им более конкретный и документированный `codex exec` путь. citeturn18search28

## Речь, база знаний и генерация ответа

Для локального speech-to-text в исследовании оставлены несколько классов решений, а не один «победитель».

**Whisper** остаётся полезным эталонным мультиязычным ASR, но вокруг него существуют значительно более deployment-friendly реализации. `whisper.cpp` ориентирован на локальный C/C++ inference, CPU execution, quantization и разные аппаратные backends; `faster-whisper` использует CTranslate2 и предназначен для более эффективного Whisper inference. citeturn1search2turn1search0turn1search1

Отдельно включены **Vosk** и **sherpa-onnx**. У Vosk есть offline русские модели, включая компактные варианты, а механизм адаптации словаря особенно интересен для фамилий преподавателей, кафедр, аббревиатур и номеров аудиторий. citeturn1search3turn1search6turn1search15 Sherpa-onnx работает полностью локально через ONNX Runtime и имеет актуальные русскоязычные ASR-модели и streaming-варианты. citeturn11search16turn11search4turn11search25

Для более тяжёлого NVIDIA-стека оставлен **Riva ASR**: актуальная документация включает русский `ru-RU` и поддерживает streaming/offline сценарии. citeturn11search0

Но для физического терминала одного ASR недостаточно. **Silero VAD** можно использовать для обнаружения начала/окончания речи, а WebRTC Audio Processing-подобный слой — для echo cancellation, noise suppression и gain control. Это особенно существенно именно в вашем сценарии: микрофон терминала физически будет слышать собственный TTS через динамики. Silero VAD поддерживает ONNX/PyTorch и 8/16 kHz аудио; существуют актуальные bindings WebRTC audio processing с AEC/NS/AGC. citeturn19search1turn19search0

Для базы знаний я отдельно развёл **парсинг, хранение, retrieval, embeddings и reranking**, чтобы Codex не воспринимал «RAG» как одну библиотеку.

Для ingestion особенно интересен **Docling**: он локально разбирает PDF, DOCX, PPTX, XLSX, HTML, изображения и другие форматы, умеет работать со структурой страниц, reading order, таблицами и OCR и экспортировать структурированные представления. Для деканатской базы с приказами, таблицами, памятками и сканированными PDF это заметно полезнее простого извлечения plaintext. citeturn15search2turn15search8turn15search16

В качестве storage/search вариантов в файл включены Qdrant, Chroma, Milvus/Milvus Lite, FAISS, PostgreSQL+pgvector и SQLite FTS5. В частности, Qdrant документирует hybrid dense+sparse retrieval и fusion, что позволяет не терять точные номера групп, аудиторий, дисциплин и фамилии из-за одного только semantic search. citeturn6search4turn6search20

Для русского и смешанного корпуса отдельно отмечены:

- **BGE-M3** — более 100 языков, dense/sparse/multi-vector retrieval и контекст до 8192 токенов; citeturn8search0
- **Qwen3-Embedding** — семейство нескольких размеров с поддержкой более 100 языков; citeturn8search23
- **multilingual-e5-large** — полезный независимый мультиязычный baseline. citeturn7search0turn7search3

После первичного retrieval можно использовать BGE/Qwen/Jina rerankers. Это вынесено в отдельный слой, чтобы будущий агент мог менять embedding и reranking независимо и сравнивать их на одном наборе университетских вопросов. citeturn7search1turn8search19

В документе описаны и три разных способа состыковать Knowledge Base именно с Codex:

```text
A. Приложение само ищет факты
   → передаёт Codex только retrieved evidence

B. Codex вызывает read-only KB MCP
   → search/get_fragment/get_document

C. Простой prototype
   → Codex читает выделенный каталог / SQLite read-only
```

Codex официально поддерживает внешние MCP-серверы, а Skills и Plugins умеют связывать инструкции с MCP tools, поэтому вариант собственного `kb.search`/`kb.get_fragment` — не обходной hack, а штатная модель расширения Codex. citeturn18search15turn18search3turn18search24

Для API-ветки в исследовании специально не использован устаревающий Assistants-подход: вынесены Responses API, function calling и Structured Outputs, чтобы возвращать тот же контракт ответа, что и у `codex exec`.

## Голос и фотореалистичный аватар

По TTS я не ограничивал список одним решением.

**Silero TTS** остаётся интересным лёгким local-first вариантом для русского. Актуальные релизы Silero продолжают поддерживать русский и ряд языков России/СНГ; проект также имеет механизмы контроля пауз/просодии. citeturn2search0turn2search7turn2search11

Более тяжёлый класс — **CosyVoice**, где есть multilingual TTS, streaming и zero-shot/cross-lingual voice cloning; русский входит в поддерживаемый набор языков. citeturn2search3turn12search2

Ещё один вариант — **Fish Speech**, особенно в области expressive speech/voice cloning, но я специально отметил в файле лицензионный caveat: у Fish Audio для моделей существуют собственные условия, поэтому нельзя автоматически переносить лицензию GitHub-кода на конкретный checkpoint и публичное использование голоса. citeturn2search2turn2search6

Старый Piper тоже зафиксирован, но именно как предупреждение: исходный `rhasspy/piper` был архивирован в октябре 2025 года, поэтому старые туториалы, которые предлагают его как активно поддерживаемый upstream, уже неактуальны. citeturn2search1

Для аватара выявились два принципиально разных технологических класса.

### Полноценный realtime 3D

**MetaHuman + Unreal Engine** уже содержит большую часть того, что требуется для вашего описания персонажа. MetaHuman предназначен для создания fully rigged photorealistic digital humans, а современный MetaHuman Creator интегрирован непосредственно в Unreal Engine. citeturn14search0turn14search3

При этом не обязательно искать генеративную нейросеть, которая одновременно «рисует человека, заставляет его встать и синхронизирует губы». В Unreal движение тела может быть обычной animation state machine:

```text
сидит
  ↓
анимация вставания
  ↓
standing idle / speaking
  ↓
анимация посадки
  ↓
сидит
```

Unreal позволяет применять Animation Sequences и Animation Blueprints к телу MetaHuman, поэтому требование «сидит за ноутбуком → встаёт перед ответом» может быть обычной заранее подготовленной body animation. citeturn14search14

Для лица поверх этого можно использовать **NVIDIA Audio2Face-3D**. Актуальный открытый SDK получает speech audio и генерирует facial animation; он использует CUDA/TensorRT, поддерживает interactive processing и имеет Unreal integration, включая пример работы с MetaHuman. NVIDIA публикует код SDK под MIT, но лицензии весов и отдельных assets необходимо проверять отдельно. citeturn21search0turn21search2

Это означает, что связка технически может выглядеть так:

```text
TTS WAV/stream
      ↓
Audio2Face-3D
      ↓
facial blendshapes
      ↓
MetaHuman
+
Animation Blueprint
      ↓
Unreal renderer
```

MetaHuman Animator тоже имеет audio-driven facial animation, поэтому в файле он оставлен как альтернативный механизм, а не только Audio2Face. citeturn14search1turn14search8

### Гибридное фотореалистичное видео

Другой класс — не рендерить сложного человека в 3D каждый кадр. **MuseTalk** рассчитан на real-time lip-sync в уже существующем видео и не является full-body generator. Именно поэтому он потенциально хорошо сочетается с заранее подготовленными роликами «сидит», «встаёт», «говорит», «садится»: тело берётся из видео, а runtime меняет область лица/губ под новый TTS. citeturn3search2turn3search5

Для такого pipeline в исследование включены FFmpeg/GStreamer, MuseTalk и LivePortrait-подобные инструменты. Это не объявлено лучшим вариантом — в файле он сохранён как отдельный класс для сравнительного прототипа с Unreal.

Также оставлены hosted-варианты вроде Synthesia Interactive Avatars и Tavus CVI как точки сравнения по качеству/latency, хотя их облачная природа создаёт отдельные вопросы по стоимости, сети и приватности. Synthesia действительно предлагает отдельный interactive avatar API, а Tavus развивает conversational video interface. citeturn13search2turn13search0

## Инструменты, MCP и Skills для самого Codex-агента

Отдельный большой раздел файла посвящён не runtime, а именно тому, **чем оснастить Codex до начала разработки**, что соответствует исходной цели исследования.

В список вошёл официальный **GitHub MCP Server**, который предоставляет агентам repository/code/issues/PR capabilities. citeturn17search0

Для UI/e2e — официальный **Microsoft Playwright MCP**, позволяющий агенту управлять браузером через структурированные accessibility snapshots. citeturn17search1

Для актуальной документации — **Context7 MCP**, специально предназначенный для передачи coding agent актуальных version-specific docs и примеров вместо опоры только на знания модели. citeturn17search3turn17search7

Официальный MCP Registry также внесён в справочник как каталог для дальнейшего поиска серверов, но с пометкой, что на текущую дату Registry находится в preview и найденные там серверы всё равно необходимо проверять по издателю и permissions. citeturn17search2

Для Codex Skills в документе уже предложены заготовки областей:

```text
kb-ingestion
rag-eval
asr-benchmark
avatar-smoke-test
kiosk-e2e
privacy-security
release-terminal
```

Это не выбор технологий, а способ заранее дать агенту повторяемые процедуры. OpenAI определяет Skill как пакет инструкций и supporting resources для конкретной задачи; Plugins могут объединять Skills и MCP connections. citeturn18search7turn18search3

Также зафиксирован `AGENTS.md`, в котором имеет смысл держать постоянные инварианты вроде «не менять embedding без benchmark», «не удалять provenance», «ответ должен возвращать source IDs или `need_human=true`».

Для CI включён официальный **Codex GitHub Action**, который оборачивает `codex exec`, позволяет задавать sandbox и сохранять финальный output как artifact. citeturn18search8 Для дополнительного security scanning отмечен Codex Security SDK. citeturn18search13

Наконец, для RAG regression testing включён **Ragas**: он предоставляет метрики faithfulness, context precision, context recall, answer correctness и experiment workflows. В частности, faithfulness оценивает, насколько утверждения ответа поддерживаются retrieved context — ровно тот класс ошибки, который опасен в деканатском ассистенте. citeturn21search1turn21search7turn21search10turn21search12

## Актуальность, приватность и что специально не было объявлено «лучшим»

Исследование зафиксировано на **19 октября 2026 года**. Это существенно для Codex/OpenAI части: интерфейсы и model lineup в 2026 году менялись, поэтому в MD отдельно внесён список устаревших механизмов и рекомендация не хардкодить конкретное имя модели там, где можно использовать поддерживаемый default.

Отдельно отмечен privacy-разрыв между «локальный ASR» и «полностью локальная система». Если ASR, vector DB и TTS находятся на терминале, но ответ формирует Codex с ChatGPT-аутентификацией, текст вопроса и переданный retrieval context всё равно обрабатываются OpenAI. OpenAI сообщает, что ChatGPT data controls распространяются на Codex; для личных аккаунтов можно отключить использование новых разговоров для улучшения моделей, а для Business/Enterprise/Edu организационные данные по умолчанию не используются для обучения. citeturn20search6turn20search1turn20search2turn20search13

Поэтому в файле намеренно **нет итоговой рекомендации вроде «берите faster-whisper + Qdrant + MetaHuman»**. Вместо этого там подготовлены четыре сравнительных класса стендов:

```text
local-first 3D
local/lightweight video-avatar
OpenAI API control
fully offline control
```

Именно так будущий Codex сможет уже в репозитории измерить ASR WER, retrieval quality, end-to-end latency, TTS latency, avatar FPS/VRAM и фактическую точность, а решение принимать после benchmark, а не после очередного исследования рынка.
