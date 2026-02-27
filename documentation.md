# Techniczna Dokumentacja Projektu (Chatterbox Flask Server PL)

## Zmiany w Modułach Systemu (Luty 2026)

Projekt przeszedł gruntowną modernizację systemu oczyszczania ścieżek dźwiękowych ("Audio Pipeline") oraz rozbudowę warstwy zarządzania logami i kolejkami.

### 1. Zastąpienie edytora CLI `auto-editor` natywnym Python Numpy/Librosa

Ze względu na problemy z niespodziewanym ucinaniem dźwięków przez skrypt Auto-Editor, usunięto z kodu binarne żądania na rzecz wysoce precyzyjnego algorytmu wyciszania opartego na `librosa.effects.split()`.

- Znajduje się on wyekstrahowany w pliku `flask_app/artifacts.py`.
- Działa na surowych próbkach Audio (Numpy Tensors). Złapany próg graniczny poniżej decybeli, podawany w wartości z progu ok. 4%-10% z ustawień interfejsu przeglądarkowego ignoruje wadliwe ścieżki i skleja wyłącznie poprawne fragmenty tła, minimalizując tzw. wdechy, długie cisze czy zacięcia (stutter) z precyzją rzędu milisekund.

### 2. Denoising Audio poprzez `pyrnnoise`

Aplikacja została wyposażona w model czyszczący szumy RNNoise z najnowszym API, potrafiący łatać ramki wielkości `480Hz` (`denoise_chunk`). Siła mieszanki zrekonstruowanego, bezszumnego nagrania jest kontrolowana z poziomu _Głównych Ustawień_ TTS.

### 3. Weryfikacja dokładności mowy (OpenAI Whisper)

Proces tworzenia książek audio może czasami powodować tzw. "halucynacje", to znaczy generowanie zupełnie innego słowa niż zalecono.
W pliku `artifacts.py` wprowadzono instrukcje pobierające najnowszą wygenerowaną treść audio i porównujące transkrypcję z modelami CTranslate2 (Faster Whisper) z dostarczonym promptem. Logi ostrzegające pojawiają się dla workera do którego przypisane było zadanie.

### 4. Dynamiczny Podgląd Logów Błędów

Dodano całkowicie nową zakładkę w interfejsie przeglądarkowym o nazwie **"📄 Logi"**.

- Oferuje asynchroniczny podgląd na żywo plików \*.log przetrzymywanych w katalogu `/logs/`.
- Zakładka odczytuje bezpośrednio `worker_err.log` lub inne dzienniki błędów bez przeładowywania interfejsu, gwarantując dostęp do logów ze wszystkich procesów wielowątkowych pracujących pod szyldem usługi Supervisor. Z tych miejsc z poziomu interfejsu można te logi wyczyścić za pomocą dedykowanych API Endpoints: `DELETE /api/logs/`.

### 5. Hot-Reload Konfiguracji w Workerach

Podczas długotrwałej pracy aplikacji demona systemowego (Supervisor), workery używają buforowanych wpisów konfiguracyjnych. Aby uniknąć restartowania serwera za każdym razem po edycji progów Denoise z poziomu WWW (w config.yaml), w pliku `flask_app/worker.py` przed rozpoczęciem przetwarzania każdego chunka/rozdziału, aplikacja wymusza przeładowanie ustawień używając reguły `config_manager.load_config()`.

### Infrastruktura

Rozdzielony proces: Web Server (app.py) jako Master, nasłuchujący Redis Queue, a w tle procesy _"slave"_ sterowane Superviorem wywoływane przez wciąż odświeżany skrypt `worker_chapters.py` renderują tensorową sieć. Generowane wyniki odsyłane są do bazy klucz-wartość oraz w SQLite, informując klienta po HTTP/WS o postępie w renderowaniu. Wszelkie odczyty pobierane są natychmiastowo za pomocą paged offset z katalogu `outputs/`.
