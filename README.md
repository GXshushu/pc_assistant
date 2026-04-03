# Personal PC Assistant Project Structure

## Recommended Tech Stack

| Category | Recommended Library | Reason |
| :--- | :--- | :--- |
| **GUI** | `PySide6` + `PyQt-Fluent-Widgets` | Native Windows 11 look, high performance, highly extensible. |
| **System Stats** | `psutil`, `GPUtil` | Comprehensive cross-platform system/process monitoring. |
| **Junk Clean** | `pathlib`, `send2trash` | Safe and modern file system operations. |
| **AI** | `openai` / `ollama` | Flexible choices for Cloud (GPT) or Local (DeepSeek/Llama) LLMs. |
| **Concurrency** | `asyncio`, `QThread` | Prevents UI freezing during heavy tasks. |
| **Packaging** | `Nuitka` | Compiles to EXE with better performance than PyInstaller. |

## Project Architecture (Extensible)

```text
personal_pc_assistant/
├── main.py              # Entry point
├── core/                # Core logic
│   ├── monitor.py       # System monitoring
│   ├── cleaner.py       # Junk cleaning logic
│   ├── ai_engine.py     # AI integration
│   └── config.py        # Settings management
├── ui/                  # UI Components
│   ├── main_window.py
│   ├── components/      # Reusable widgets
│   └── resources/       # Icons, themes
├── plugins/             # Extensible plugin system
│   └── example_tool.py
├── utils/               # Helper functions
│   └── logger.py
└── requirements.txt     # Dependencies
```
