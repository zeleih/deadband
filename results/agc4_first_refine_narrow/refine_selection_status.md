# refine_selection_status.md

结果目录：`/Applications/openandes/demo/demo/deadband/results/agc4_first_refine_narrow`

## 当前收口结论
本轮 `AGC=4, init-mode=first` 的 narrow refine sweep 已在 20:00 前结束，结果文件已齐。

但**现有证据不足以直接替换默认参数**，原因是最优候选在不同指标下不一致：

- **score 最优**：`kp=0.03, ki=0.075`
  - `abs_mean_hz = 0.0073787944`
  - `rms_hz = 0.0092228663`
  - `final_hz = -0.0054963946`
- **abs_mean / rms 最优**：`kp=0.02, ki=0.05625`
  - `abs_mean_hz = 0.0073034173`
  - `rms_hz = 0.0091370506`
  - `final_hz = -0.0058154956`
- **当前默认**：`kp=0.05, ki=0.0625`
  - `abs_mean_hz = 0.0073665591`
  - `rms_hz = 0.0092019103`
  - `final_hz = -0.0056508629`

## 当前判断
- `kp=0.02, ki=0.05625` 在 `abs_mean_hz / rms_hz` 上略优于默认；
- `kp=0.03, ki=0.075` 在当前 `score` 排序上最优；
- 但两者相对默认的提升都不大，且优势指标不一致；
- 因此，**当前不建议仅凭这一轮 narrow refine sweep 就替换默认参数**。

## 已回答什么
- 已完成 sweep 收口；
- 已确认本轮不存在“明显压倒默认参数”的唯一赢家；
- 当前默认参数 `kp=0.05, ki=0.0625` 不应在没有 tie-break 复核前直接被替换。

## 下一步
已注册后续 tie-break 子任务，专门裁决：
- `kp=0.02, ki=0.05625`
- `kp=0.03, ki=0.075`
- `kp=0.05, ki=0.0625`（当前默认）

目标是给出最终单一推荐，并明确是否替换默认参数。