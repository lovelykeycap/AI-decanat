# Сторонние компоненты

Точные Python-пакеты и версии находятся в `uv.lock`. Этот список фиксирует ключевые источники и не заменяет их лицензионные тексты. Перед выпуском установщика включить необходимые уведомления и проверить права на распространение конкретных моделей/ассетов.

| Компонент | Источник | Лицензия/условие |
| --- | --- | --- |
| Whisper и whisper.cpp | openai/whisper, ggml-org/whisper.cpp | MIT; контрольные суммы установленных весов — models/installed.json |
| pywhispercpp | absadiki/pywhispercpp | MIT; включает собственную сборку whisper.cpp, закреплено в lockfile |
| WebRTC Audio Processing 1.3 | freedesktop.org/software/pulseaudio/webrtc-audio-processing | BSD-подобные условия upstream и WebRTC; COPYRIGHT/COPYING сохранены в скачанном исходнике |
| Abseil 20230125.1 | abseil/abseil-cpp, Meson wrap с фиксированными SHA-256 | Apache-2.0 |
| Silero VAD | snakers4/silero-vad | MIT; скачивается закреплённая ревизия ONNX |
| Silero TTS v5 ru | [README издателя](https://github.com/snakers4/silero-models#licence), [LICENSE](https://github.com/snakers4/silero-models/blob/master/LICENSE) | Основной LICENSE — CC BY-NC-SA 4.0; README относит модели к нему, кроме base CIS под MIT. Выбранный v5_ru не объявляется MIT. Соблюсти attribution/non-commercial и проверить применимость к окончательной эксплуатации/распространению |
| BGE-M3 | BAAI/bge-m3 | MIT; модель и ревизия фиксируются при установке |
| Unreal Engine / MetaHuman | Epic Games | Собственные условия Epic; проверить бесплатное образовательное использование и комплект распространяемых ассетов |
| NVIDIA ACE / Audio2Face | NVIDIA | Отдельно проверить код плагина, локальные веса и право распространения; MIT SDK не делает автоматически MIT все связанные материалы |
| Codex CLI и OpenAI SDK | OpenAI | Клиентский код и доступ к сервису имеют разные условия; подписка/API оплачиваются отдельно от бесплатных локальных инструментов |

Платные ассеты и сервисы аватара не покупались. Предобученные веса загружаются отдельно от исходников проекта. Собственные предметы сцены создаются скриптом; реальные сторонние персонажи/анимации пока не добавлены.

Источники и ожидаемые SHA моделей речи/поиска закреплены в `tools/model-sources.json`. Кандидат desktop-комплекта со статусами отсутствующих файлов — `assets/manifest/desktop-stack.json`. У локальных Audio2Face-весов собственная NVIDIA Open Model License: не переносить MIT с SDK на веса по умолчанию.
