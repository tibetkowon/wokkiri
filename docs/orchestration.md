# 작업 분담 (오케스트레이션)

사용자와 대화하는 채팅이 **조율자**입니다. 코드를 읽거나 고치거나 검증하는 일은 직접 하지 않고, 일마다 새 작업 세션(Orca worker)을 띄워 결과만 받아 사용자에게 요약합니다. 작업 세션은 이전 대화를 모르고 지시서에 적힌 일만 압니다. 프로젝트 공통 규칙은 `AGENTS.md`가 자동으로 적용됩니다.

## 역할과 모델

모델은 `orchestration.json`에 있습니다. 바꿀 때는 그 파일의 해당 줄만 고칩니다.

| 역할 | 하는 일 | 에이전트 · 모델 |
|---|---|---|
| reader | 파일을 읽고 질문에 답하는 요약만 합니다. 파일을 고치지 않습니다. | claude · `claude-haiku-5-5` |
| coder | 지시서 범위의 코드를 고치고 테스트를 돌립니다. | claude · `claude-fable-5-2` |
| verifier | 코드를 고치지 않고 diff와 테스트로 지시서의 완료 기준을 검증합니다. | codex · `gpt-6.1-sol` |

- 추론 강도가 필요하면 역할에 `"effort": "high"`처럼 더합니다(모델도 함께 있어야 합니다). 이 컴퓨터의 Codex 기본값은 `low`라 검증에는 올리는 편이 낫습니다.
- Codex에서 쓸 수 있는 모델 이름은 `~/.codex/models_cache.json`에서 확인합니다.

## 실행

```sh
scripts/worker.py coder <지시서 파일>
```

시작한 뒤 조율자는 턴을 끝내고, 작업이 끝나면 Orca가 이 채팅을 깨웁니다. 지시서는 저장소 밖 임시 폴더에 둡니다. 같은 작업 트리에서 coder를 동시에 둘 이상 돌리지 않으며, 병렬이 필요하면 `--worktree new-child --name <이름>`을 더합니다.

## 진행 순서

1. 필요하면 reader로 현황을 읽습니다.
2. coder에게 지시서를 줍니다. 커밋은 하지 않습니다.
3. coder가 끝나면 verifier에게 같은 지시서와 "`git diff`와 테스트로 완료 기준을 확인하라"는 요청을 줍니다.
4. 문제가 나오면 조율자가 `orchestration send`로 같은 coder에게 되돌려 고치게 합니다. 통과하면 사용자에게 결과·근거·남은 위험을 보고하고, 커밋은 사용자가 요청할 때 합니다.

## 지시서에 넣을 것

지시서는 그것만 읽어도 일을 할 수 있어야 합니다. 이전 대화를 붙이지 않습니다.

- 대상 파일과 바꿀 내용, 건드려도 되는 파일, 건드리면 안 되는 것
- 끝났다는 증거(실행할 테스트 명령과 기대 결과)
- 보고 형식: 바꾼 파일, 테스트 결과, 하지 않은 일과 남은 위험
- `HANDOFF.md`의 "다음 작업"은 조율자가 관리합니다. 지시서에 없는 항목은 하지 않습니다.

## 훅 제약 (`.claude/settings.json`, `scripts/guard.py`)

조율자 채팅에는 지침이 아니라 훅으로 아래를 강제합니다. Orca가 띄운 작업 세션은 제약을 받지 않습니다.

- **수정**: `docs/`, `AGENTS.md`, `HANDOFF.md`, `README.md`, `orchestration.json`만 고칠 수 있습니다. 저장소 밖은 임시 폴더(지시서)와 메모리 폴더만 허용하고, 훅 설정과 `~/.claude/settings.json`은 막힙니다.
- **Bash**: 줄바꿈·`$`·`;`·`&&`·`|`·`>` 없는 단일 명령만 됩니다. `git`(status, diff, log, show, rev-parse, ls-files, check-ignore, add, commit), `orca orchestration`(`worker-start` 제외), `orca skills`, `scripts/worker.py`, `ls`, `pwd`입니다.
- **모델 강제**: `orca orchestration worker-start`를 직접 치는 것은 막혀 있고, `scripts/worker.py`는 `--agent`, `--model`, `--effort`, `--terminal` 덮어쓰기를 거부합니다. 모델은 `orchestration.json`으로만 정해집니다.
- **작업 세션 인식**: 첫 지시가 "You are working inside Orca, a multi-agent IDE. You are a dispatched worker."로 시작하는 세션을 `UserPromptSubmit` 훅이 `~/.cache/wokkiri-guard/`에 기록합니다. Orca가 문구를 바꾸면 작업 세션도 막히니(멈추는 쪽으로 실패) `scripts/guard.py`의 `WORKER_PROMPT`를 고칩니다.
- **훅이 실패하면 막습니다**(`onFailure: block`). 차단 기록은 `~/.cache/wokkiri-guard/log`에 남습니다.
- **막지 못하는 것**: `Read`·`Grep`·`Glob`(코드 읽기), MCP 도구(원격 저장소 쓰기 등), Codex 세션, `orca` 명령이 여는 통로입니다. 위험은 실수 방지 수준이며 악의적 우회까지 막지는 못합니다.
- **적용과 해제**: 새로 여는 세션부터 적용됩니다. 이미 열린 채팅은 `/hooks`를 한 번 열거나 다시 시작해야 합니다. 끄려면 `.claude/settings.json`의 `hooks`를 지우거나 `/hooks`에서 끕니다.
