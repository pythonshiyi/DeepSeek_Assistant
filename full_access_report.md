# full-access diagnosis report

> read-only, no source modified

## deepseek_client.py
- exists: True
- size: 91630 B

## permissions.py
- exists: True
- size: 12157 B

## config.json
- exists: True
- size: 2475 B

## 1. config.json full_auto
- loc: line 69:   "full_auto": true,
- full_auto=true: True

## 2. permissions.py FULL_AUTO
- loc: line 23: FULL_AUTO = False  # 完全智能模式：允许目录内全自动（免审批/免开关），系统阻止列表仍生效

## 3. deepseek_client.py PIP_ALLOWLIST
- loc: line 909
- contains playwright: True

## 4. deepseek_client.py run_python sandbox
- loc: line 565
- has os.listdir block: True

## 5. run_python write-open guard
- loc: line 574
