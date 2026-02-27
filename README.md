# Chatterbox TTS Server (Edycja Polska)

Wielogłosowy serwer Text-To-Speech oparty na modelu **Chatterbox** (zoptymalizowany pod kątem języka polskiego), połączony z panelem WWW we Flasku. Aplikacja oferuje zaawansowane narzędzia do zarządzania głosami, automatyczną korekcję artefaktów audio oraz wygodny system kolejkowania zadań.

## Główne Funkcje

- **Wielogłosowość**: Generowanie tekstów z wieloma mówcami, używając tagów w stylu `[narrator]Tekst[/narrator]`.
- **Modele Chatterbox**: Wsparcie dla modeli wielojęzycznych (np. `chatterbox-multilingual`) z możliwością wyboru języka.
- **Klonowanie Głosów**: Tworzenie nowych głosów poprzez "zero-shot voice cloning" bazując na 5-10 sekundowych próbkach audio (WAV, MP3, itp.).
- **Redukcja Artefaktów (Audio Pipeline)**:
  - **Denoising (RNNoise)**: Błyskawiczne odszumianie przy użyciu natywnych bibliotek sieci neuronowych.
  - **Zaawansowane obcinanie ciszy i wdechów**: Zaimplementowane silnikiem _librosa_ (zastąpiło wadliwy skrypt _Auto-Editor_ dla uniknięcia "klikania" wokalu).
  - **FFmpeg Normalizacja**: Zapewnia spójną głośność dla całego rozdziału.
- **Weryfikacja Transkrypcji**: Opcjonalne użycie modeli _faster-whisper_ (lub _openai-whisper_) do sprawdzania, czy wygenerowane audio zbiega się z tekstem w celu upewnienia się co do braku halucynacji AI.
- **Przeglądarka Logów**: Intuicyjna, bezodświeżeniowa zakładka w panelu WWW służąca do przeglądania logów z `worker_err.log` oraz natychmiastowego resetowania ich zawartości.
- **Skalowalne Worker'y (Supervisor)**: System jest oparty na asynchronicznej kolejce zadań (Redis + RQ), a Supervisor dynamicznie budzi ustawioną w UI liczebność procesów tła do równoległego renderowania mowy, omijając limity pamięci (VRAM).

## Wymagania Systemowe

Aplikacja jest przystosowana do działania w systemie **Linux** z zainstalowanym sterownikiem procesora GPU rzędu NVIDIA / z biblioteką CUDA 11+.

- Python 3.10+
- Redis-Server
- Supervisor
- NVIDIA Toolkit (CUDA)

## Instalacja Krok po Kroku

### 1. Pobranie i instalacja repozytorium

Skopiuj repozytorium do wybranego folderu, a następnie pobierz środowisko wirtualne (_venv_):

```bash
git clone <adres-repo>
cd Chatterbox-TTS-Server
python3.10 -m venv venv
source venv/bin/activate
```

### 2. Zależności projektu

Program wymaga instalacji pakietu z bibliotekami systemowymi oraz paczkami Pythona:

```bash
sudo apt update
sudo apt install redis-server supervisor ffmpeg
pip install -r requirements.txt
# Upewnij się, że masz PyTorch z CUDA (wewnątrz requirements-nvidia.txt powinno znajdować się poprawne dowiązanie)
```

### 3. Konfiguracja Supervisora

Aplikacja wykorzystuje oprogramowanie Supervisor pod rygorem roota (`sudo`) by odpalac w tle renderujące procesy workerów.

Utwórz plik wymiany zadań dla Supervisor-a wpisując nową regułę do konfiguracji systemowej, dla przykładu w `/etc/supervisor/conf.d/chatterbox_workers.conf`:

```ini
[program:chatterbox_workers]
command=/home/twój-user/TTS/Chatterbox-TTS-Server/venv/bin/python worker_chapters.py
directory=/home/twój-user/TTS/Chatterbox-TTS-Server
autostart=true
autorestart=true
numprocs=2
process_name=%(program_name)s_%(process_num)02d
stderr_logfile=/home/twój-user/TTS/Chatterbox-TTS-Server/logs/worker_err.log
stdout_logfile=/home/twój-user/TTS/Chatterbox-TTS-Server/logs/worker_out.log
```

Następnie załaduj nowe reguły:

```bash
sudo supervisorctl reread
sudo supervisorctl update chatterbox_workers
```

Wyżej wskazana liczebność procesów `numprocs=2` ulega na żywo nadpisaniu, gdy zmienisz limit w zakładce _Ustawienia_ w panelu WWW tej aplikacji.

### 4. Baza Danych i Środowisko

Utwórz plik konfiguracyjny środowiska `.env` w głównym katalogu, wpisując do niego preferowany wariant domyślny liczebności programów działających w tle:

```env
NUM_WORKERS=2
```

## Uruchamianie

Każdorazowo, aby uruchomić sam silnik webkowy (np. w oknie `screen` lub `tmux`), uruchom go po prostu standardowym poleceniem Pythona, podczas gdy upewnisz się, że Redis oraz Supervisor natywnie działają w systemie domyślnie.

```bash
source venv/bin/activate
python app.py
```

Wejdź przeglądarką pod wskazany port, domyślnie [http://127.0.0.1:5000/](http://127.0.0.1:5000/). Z tego poziomu zarządzasz procesem tworzenia tekstów, konwersji modeli i odsłuchami bibliotek.

## Struktura Aplikacji

- `app.py`: Główny plik wywoławczy weeb-servera.
- `engine.py`: Abstrakcje generacyjne LLM pod TTS-a (sterowanie inferencją).
- `flask_app/`: Widoki i kontrolery dla Flask Application, szablony HTML i style JS/CSS.
- `flask_app/artifacts.py`: Moduł oczyszczającym audio (RNNoise / Librosa silence-split / FFmpeg norm).
- `worker_chapters.py`: Demon odbierający małe fragmenty chunków z zadania RQ Redis i zlecający ich wygenerowanie `engine.py`.
- `logs/`: Folder rzucający logi tekstowe (worker_err.log) wyświetlane dla użytkownika do zakładki "Logi" w interfejsie.
- `config.py`: Parser domyślnego pliku konfiguracji `config.yaml` definiujący preferencje użytkownika na temat generowania.

---

_Aplikacja została oparta na modelach konwersacyjnych Chatterbox._ W razie awarii modelu GPU / zwiech, zajrzyj do nowej zakładki **Logi** poprzez WebGUI.
