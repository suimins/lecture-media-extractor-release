# Lecture Media Extractor

Lecture Media Extractor saves your choice of MP4 video, MP3 audio, and transcripts. Select one or more formats in the Chrome extension; selected files are kept and only temporary processing files are removed. The automatic Chrome workflow supports Windows, macOS, and Linux, with native GPU detection and CPU fallback. The existing URL sniffer and Python CLI are also available.

## 자동 처리: MP4 · MP3 · 스크립트 선택 다운로드

Chrome에서 한성 eClass 강의 창을 열면 확장에서 체크한 **MP4 영상·MP3 음성·스크립트**를 자동으로 저장합니다. 기본값은 세 항목 모두 선택이며, 필요한 항목만 체크할 수 있습니다.

### 처음 설치

Windows 친구에게 공유할 때는 **`Lecture-Script-Windows-0.5.1.zip`**을 전달합니다. 압축을 전부 풀고 **`START-HERE.cmd`**를 실행하면 `%LOCALAPPDATA%\LectureScript`에 프로그램을 복사하고, 전용 Python·pip 패키지·GPU 처리기·Chrome Native Messaging 연결을 준비합니다. Chrome이 없으면 Google의 서명을 확인한 공식 설치 프로그램을 실행합니다. 설치 후 확장 관리 화면과 실제 폴더 경로를 복사할 수 있는 한국어 안내를 엽니다.

일반 Windows Chrome에서 스토어 미등록 확장은 **개발자 모드 → 압축해제된 확장 로드 → 안내된 `extension` 폴더 선택**을 한 번 직접 해야 합니다. 설치 프로그램은 Chrome 프로필이나 기업 정책을 변경하지 않습니다. 이 설치판은 Windows 10/11 64비트용이며 인터넷 연결이 필요합니다. 설치된 앱 폴더를 유지하면 받은 ZIP과 압축을 푼 폴더는 정리할 수 있습니다. 바탕화면의 `Lecture Script - Chrome setup`으로 안내를 다시 엽니다.

소스에서 직접 설치하거나 다른 OS를 사용할 때:

1. Mac·Linux는 **Python 3.12**를 설치합니다. Mac에서는 칩에 맞는 Python을 사용합니다. Windows 설치 파일은 전용 Python을 자동으로 준비합니다.
2. 이 저장소를 내려받아 압축을 풀고 아래 설치 파일을 실행합니다. OS와 아키텍처에 맞는 처리기를 준비하고 현재 사용자에게 Chrome 연결을 등록합니다.
3. Chrome에서 `chrome://extensions`를 열고 **개발자 모드 → 압축해제된 확장 프로그램을 로드합니다**를 누릅니다.
4. 이 저장소의 **`extension`** 폴더를 선택합니다.
5. 이미 열려 있던 eClass 과목 페이지를 새로고침하고, 강의 동영상 창을 엽니다. 영상 주소가 재생 후에 제공되면 재생을 한 번 누릅니다.

| 환경 | 설치 | 관리 화면 | 자동 전사 장치 |
| --- | --- | --- | --- |
| Windows x64 | `setup.cmd` | `start.cmd` | CUDA → Vulkan → CPU |
| macOS Apple Silicon / Intel | `setup.command` | `start.command` | Metal → CPU |
| Linux x64 | `sh setup.sh` | `.venv/bin/python -m lecture_script` | Vulkan → CPU |

Mac의 Metal 처리기는 Apple Command Line Tools가 필요합니다. 없다면 터미널에서 `xcode-select --install`로 설치한 뒤 `setup.command`를 다시 실행하세요. 설치 중 GPU 준비에 실패해도 CPU용 설치와 Chrome 연결은 진행하며, 실패 원인을 표시합니다. `.command` 실행 권한이 없다면 터미널에서 `sh setup.command`로 실행할 수 있습니다. Linux GPU에는 그래픽 드라이버와 Vulkan 로더가 필요합니다.

설치 화면 안내: [install-guide.html](install-guide.html). 앱의 사용자별 Chrome 등록에는 관리자 권한이 필요하지 않습니다. 프로젝트 폴더를 옮기면 Windows는 `install-chrome.cmd`, Mac은 `install-chrome.command`, Linux는 `.venv/bin/python install_chrome.py`로 다시 등록하고 확장도 새 경로에서 다시 로드합니다.

Windows ZIP은 `python scripts/build_windows_zip.py`로 만듭니다. 앱·확장·설치 파일만 포함하며, `.venv`, 모델, 강의 파일, 로그, Git 기록은 포함하지 않습니다. Windows의 Python 자동 준비에는 고정 릴리스와 SHA-256을 검증한 [uv 0.12.23](https://github.com/astral-sh/uv/releases/tag/0.12.23) 및 [python-build-standalone](https://docs.astral.sh/uv/guides/install-python/)을 사용합니다. Python은 앱의 `data/python`에 설치하고, 필요한 패키지는 해당 가상환경의 `pip`로 설치합니다. GUI 의존성은 사용하는 Qt 모듈이 포함된 PySide6-Essentials로 제한합니다.

**이전 버전에서 업데이트할 때도 해당 OS의 설치 파일을 다시 실행하세요.** GPU 처리기와 Chrome 연결을 함께 갱신한 뒤 확장을 새로고침합니다.

자동 처리 확장은 `extension`입니다. 아래의 `m3u8-sniffer-extension`은 주소 복사와 과목 목록 수집을 위한 별도 확장입니다. 자동 처리에는 설치하지 않아도 됩니다.

### 사용 및 저장 위치

- 확장 아이콘의 **다운로드할 파일**에서 MP4 영상, MP3 음성, 스크립트를 선택하고 **선택한 파일 다운로드 / 재시도**를 누릅니다. 자동 시작에도 같은 선택을 사용합니다.
- 선택은 확장 팝업을 닫아도 유지되고 다음 작업부터 적용됩니다. 하나도 선택하지 않으면 작업을 시작하지 않습니다.
- 결과는 **다운로드 폴더/lecture/과목명/** 아래의 `video/*.mp4`, `audio/*.mp3`, `script/강의명/`에 저장됩니다. Windows에서는 등록된 다운로드 폴더를, Mac·Linux에서는 `~/Downloads`를 사용합니다.
- **선택한 결과물은 삭제하지 않습니다.** 선택한 출력이 모두 완료되면 임시 영상·WAV·작업 파일만 정리합니다. 이후 선택을 바꿔도 앞서 저장한 결과와 로컬 원본은 보존합니다.
- MP4와 MP3는 실제 해당 형식으로 저장합니다. 스크립트를 해제하면 음성 인식을 실행하지 않고 모델도 내려받지 않습니다. MP4만 선택하면 음성 추출도 생략합니다.
- 대본 폴더에는 `transcript.txt`, `transcript.md`, `subtitles.srt`, `subtitles.vtt`, JSON 메타데이터가 생성됩니다.
- 과목명은 강의 창 또는 그 창을 연 과목 페이지에서 읽습니다. 찾지 못하면 `과목 미지정`에 저장하며, 확장 팝업에서 과목명을 입력하고 재시도할 수 있습니다.
- 완료된 강의를 다시 열면 현재 선택한 파일이 모두 있을 때 기존 결과를 사용합니다. 선택을 추가하거나 파일이 누락되면 다시 처리합니다. 저장된 MP4가 있으면 재사용하며, 실패·중단된 작업의 임시 파일과 완료한 전사 구간도 재시도에 사용합니다.
- **처리 중에는 Chrome을 켜두세요.** 강의 탭이나 확장 팝업은 닫아도 됩니다. Chrome을 완전히 종료하면 처리가 중단됩니다.
- 위 표의 관리 화면 실행 파일로 저장 기록을 확인하거나 내 파일을 스크립트로 변환할 수 있습니다. MP4·MP3 선택 다운로드는 크롬 확장에서 설정합니다. 자동 처리에는 관리 화면을 켜둘 필요가 없습니다.

자동 시작은 `https://learn.hansung.ac.kr/mod/vod/viewer.php?id=...`에 적용됩니다. 다른 사이트에서는 확장의 **선택한 파일 다운로드 / 재시도**를 사용할 수 있습니다. MP4, HLS, DASH 감지는 플레이어 방식에 따라 달라집니다. 자동 처리 확장은 다른 강의를 직접 열거나 과목 목록을 순회하지 않습니다.

음성 인식은 로컬에서 실행하고, 모델은 처음 사용할 때 내려받습니다. 설치 프로그램은 OS와 아키텍처에 맞는 처리기를 준비하고 **실제 지원하는 GPU API**를 확인합니다. Windows에서는 AMD·Intel·NVIDIA 등에 Vulkan을 사용하고, CUDA 장치가 있으면 CUDA 라이브러리도 설치합니다. Mac에서는 Apple Silicon뿐 아니라 Intel Mac에서도 Metal 장치를 확인합니다. Linux x64는 Vulkan을 사용합니다.

전사는 위 표의 순서로 실제 실행 가능한 장치를 선택합니다. Vulkan에서는 외장 GPU를 내장 GPU보다 먼저 시도하고, Metal에서는 macOS의 기본 Metal 장치를 사용합니다. 감지 후 짧은 추론까지 성공해야 GPU로 표시하며, 실패 원인을 표시한 뒤 다음 장치를 시도합니다. 진행 상황에는 `GPU (CUDA)`, `GPU (VULKAN)`, `GPU (METAL)` 또는 `CPU`가 표시됩니다. GPU가 없거나 모두 초기화에 실패하면 CPU로 처리합니다.

모델 크기는 사용자가 선택한 값을 유지하며, GPU 사용률에 따라 모델을 실시간 변경하지 않습니다. MP4 저장과 MP3 변환은 별도 작업이므로 이 단계에서는 GPU 사용량이 낮을 수 있습니다. Vulkan GPU는 드라이버의 Vulkan 지원이 필요합니다. CUDA와 Vulkan 모델은 형식이 달라 각각 처음 사용할 때 다운로드하며, 모델은 Git에 포함하지 않습니다.

Windows·Linux의 Vulkan 처리기는 [whisper.cpp 커뮤니티 빌드 v1.8.4.1](https://github.com/jiang1997/whisper.cpp-release/releases/tag/v1.8.4.1)를 사용합니다. 고정된 릴리스 URL과 SHA-256을 검증하며, Windows는 CLI만 추출하고 Linux는 필요한 공유 라이브러리를 함께 설치합니다. Mac은 [upstream whisper.cpp v1.8.4](https://github.com/ggml-org/whisper.cpp/tree/v1.8.4)의 고정 커밋·SHA-256을 확인한 후, 현재 아키텍처에 맞게 Metal을 활성화하여 빌드합니다. Intel Mac에서도 Metal을 끄지 않으며, 셰이더를 실행 파일에 포함합니다.

CUDA 패키지만 수동으로 설치하려면 다음 명령을 사용합니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

영상 다운로드에 필요한 로그인 쿠키와 요청 헤더는 해당 영상·페이지 범위에서 로컬 처리기로 전달합니다. 인증 정보는 디스크에 저장하지 않습니다. 모델·다운로드 결과·작업 기록은 Git에 포함하지 않습니다.

### 문제 해결

- **로컬 처리기 연결 오류:** 위의 OS별 Chrome 등록 파일을 다시 실행하고, 확장을 새로고침한 뒤 강의 창에서 재시도합니다.
- **영상 감지 대기:** 과목 페이지와 강의 창을 새로고침하고 필요하면 재생을 누릅니다.
- **주소 만료 / 403:** 로그인 상태에서 강의 창을 새로 열고 재시도합니다.
- **전사 실패:** 확장에 표시된 오류를 확인합니다. 완료된 영상과 음성은 보존되므로 재시도할 수 있습니다.

### 자동 처리 개발 검증

```powershell
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check extension/background.js
node --check extension/content.js
node --check extension/popup.js
```

브라우저 테스트는 로컬 서버와 모의 학교 페이지를 사용합니다. CI에서는 Windows, Apple Silicon Mac, Intel Mac, Linux에서 테스트하며, Mac·Linux에서는 설치 파일과 실제 Native Messaging 실행기를 실행하고 공개 음성 샘플로 모델 추론·내보내기를 확인합니다. 실행 환경에 GPU가 노출되지 않으면 CPU 경로를 검증하고 이를 로그에 명시합니다. 실제 학교 로그인·플레이어는 별도 확인이 필요합니다.

## Manual URL Sniffer And CLI

Use it only with lectures or media you are allowed to access and process. DRM-protected streams are not supported.

The manual workflow has two parts:

- `m3u8-sniffer-extension`: Chrome/Edge extension that detects lecture `.m3u8` playlist and subtitle URLs.
- `m3u8`: Python CLI pipeline that uses subtitles when available, or extracts audio with ffmpeg and transcribes it with faster-whisper.

The release folder intentionally excludes Whisper models, virtual environments, ffmpeg binaries, logs, generated transcripts, and private/signed lecture URLs.

## Folder Layout

```text
lecture-media-extractor-release/
  lecture_script/             Automatic pipeline and local file manager
  extension/                  Automatic Chrome extension
  tests/                      Automatic pipeline regression tests
  m3u8/                       Python transcript CLI
  m3u8-sniffer-extension/     Chrome/Edge Manifest V3 extension
  examples/                   Safe placeholder examples
  scripts/                    Convenience scripts
  tools/                      Optional local tools, not committed
```

## Requirements

- Python 3.11 or newer
- ffmpeg and ffprobe on `PATH`, or explicit paths passed with `--ffmpeg` and `--ffprobe`
- Chrome or Edge for the URL sniffer extension
- Enough disk space for model cache and transcript outputs

CPU transcription works by default. CUDA/GPU transcription can be faster, but it depends on local NVIDIA driver/CUDA/cuDNN compatibility.

## Install Python App

From the repository root:

```powershell
cd .\m3u8
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
python -m app.main --check-ffmpeg
```

After installation, the CLI is also available as:

```powershell
m3u8-transcript --check-ffmpeg
```

If ffmpeg is not on `PATH`, install it separately and pass the binary paths:

```powershell
python -m app.main --check-ffmpeg --ffmpeg "C:\path\to\ffmpeg.exe" --ffprobe "C:\path\to\ffprobe.exe"
```

## Install Browser Extension

1. Open `chrome://extensions` or `edge://extensions`.
2. Enable developer mode.
3. Click `Load unpacked`.
4. Select `m3u8-sniffer-extension`.

Open a lecture page, start playback, then use the extension popup to copy a media URL or scan a supported course page queue.

## Run One URL

```powershell
cd .\m3u8
.\.venv\Scripts\Activate.ps1
python -m app.main --run-url "https://example.com/path/index.m3u8" --out ..\outputs\one --whisper-model medium
```

Useful options:

```powershell
python -m app.main --run-url "https://example.com/path/index.m3u8" `
  --out ..\outputs\one `
  --whisper-model large-v3 `
  --whisper-device cuda `
  --whisper-compute-type int8_float16 `
  --whisper-beam-size 5 `
  --language ko
```

## Run Batch

Put one m3u8 URL per line in a text file. Lines starting with `#` are ignored.

```powershell
.\scripts\run-batch.ps1 -Urls .\examples\urls.example.txt -Out .\outputs\course -Model medium
```

For CUDA:

```powershell
.\scripts\run-batch.ps1 -Urls .\examples\urls.example.txt -Out .\outputs\course -Model large-v3 -Device cuda -ComputeType int8_float16 -BeamSize 5
```

Each item writes:

- `transcript.txt`
- `transcript.md`
- `transcript.srt`
- `transcript.json`

The batch summary is written to `batch_summary.json`.

## Model Notes

Models are not included in this release. faster-whisper downloads or uses the requested model under `m3u8/model-cache/whisper-models` by default.

Common choices:

- `tiny`: very fast, useful for smoke tests
- `medium`: balanced
- `large-v3`: better quality, heavier

For Korean technical lectures, `large-v3` with later LLM cleanup usually gives the best study notes, especially for domain terms.

## Privacy And Sharing

Do not commit:

- real signed CDN URLs
- cookies or authorization headers
- generated transcripts from private lectures
- downloaded models
- local ffmpeg binaries

Generated metadata redacts common signed URL query parameters, but private transcripts and raw captured manifests should still be treated as private.

The browser extension uses broad `<all_urls>` permissions because lecture players and CDN hosts vary. It stores detections in browser session storage and does not send them to a remote server.

Before publishing to GitHub, run:

```powershell
git status
git diff --stat
```

Then check that only source, docs, examples, and scripts are staged.

## License

MIT. See `LICENSE`.
