# fixcheck_objective_and_findings.md

## 任务目标
将 2026-03-16 17:20–17:31 期间产生的 `h13d2_fixcheck_*` 文件明确归档为**后续验证任务**，而不是 `sim-20260314-1048-rootcause-drilldown` 根因深挖主任务的一部分。

本次 fixcheck 的目标是：
1. 在 AGC=4 条件下，对 h13d2 的若干 `kp/ki` 参数点做 bugfix 后复核；
2. 汇总各参数点的频率指标；
3. 对能与旧结果对应的参数点做 old-vs-new 对比；
4. 判断哪些参数点仍可接受，哪些参数点已经发散。

## 交付物
位于 `/Applications/openandes/demo/demo/deadband/results/`：
- `h13d2_fixcheck_kp005_ki00625_agc4_dispatch.json`
- `h13d2_fixcheck_kp005_ki00625_agc4_frequency.csv`
- `h13d2_fixcheck_kp005_ki00625_agc4_frequency.png`
- `h13d2_fixcheck_kp01_ki0025_agc4_dispatch.json`
- `h13d2_fixcheck_kp01_ki0025_agc4_frequency.csv`
- `h13d2_fixcheck_kp01_ki0025_agc4_frequency.png`
- `h13d2_fixcheck_kp02_ki005_agc4_dispatch.json`
- `h13d2_fixcheck_kp02_ki005_agc4_frequency.csv`
- `h13d2_fixcheck_kp02_ki005_agc4_frequency.png`
- `h13d2_fixcheck_kp03_ki0075_agc4_dispatch.json`
- `h13d2_fixcheck_kp03_ki0075_agc4_frequency.csv`
- `h13d2_fixcheck_kp03_ki0075_agc4_frequency.png`
- `h13d2_fixcheck_kp04_ki01_agc4_dispatch.json`
- `h13d2_fixcheck_kp04_ki01_agc4_frequency.csv`
- `h13d2_fixcheck_kp04_ki01_agc4_frequency.png`
- `h13d2_fixcheck_after_agc_bugfix_summary.csv`
- `h13d2_fixcheck_after_agc_bugfix_vs_old_summary.csv`
- `h13d2_fixcheck_after_agc_bugfix_compare.png`

## 截止时间
- 本轮 fixcheck 文件落盘时间窗：`2026-03-16 17:20:13` 至 `2026-03-16 17:31:30`（Asia/Shanghai）
- 本轮任务按文件产出计，已在 `2026-03-16 17:31:30+08:00` 形成汇总交付。

## 当前结论
### 1. bugfix 后可接受的参数点
来自 `h13d2_fixcheck_after_agc_bugfix_summary.csv`：

- `kp=0.05, ki=0.0625`
  - `min_hz=-0.0632966672`
  - `max_hz=0.0311049048`
  - `final_hz=-0.0056494262`
  - `abs_mean_hz=0.0076271681`
  - `rms_hz=0.0099008981`
- `kp=0.1, ki=0.025`
  - `min_hz=-0.0632966672`
  - `max_hz=0.0355414101`
  - `final_hz=-0.0042683267`
  - `abs_mean_hz=0.0085700263`
  - `rms_hz=0.0111012547`
- `kp=0.2, ki=0.05`
  - `min_hz=-0.0632966672`
  - `max_hz=0.0380194115`
  - `final_hz=-0.0054420789`
  - `abs_mean_hz=0.0085356274`
  - `rms_hz=0.0109832691`
- `kp=0.3, ki=0.075`
  - `min_hz=-0.0632966672`
  - `max_hz=0.0447842667`
  - `final_hz=-0.0025835780`
  - `abs_mean_hz=0.0116432700`
  - `rms_hz=0.0147035574`

以上四组均未表现出明显失稳，可作为 bugfix 后的有效复核样本。

### 2. 明确失稳/拒绝的参数点
- `kp=0.4, ki=0.1`
  - `min_hz=-820.5011564751`
  - `max_hz=0.2757445853`
  - `final_hz=-820.5011564751`
  - `abs_mean_hz=364.3397758382`
  - `rms_hz=445.9817299973`

该组结果已明显发散，不能作为正常 fixcheck 成果使用，应直接标记为**失稳/拒绝**。

### 3. 与旧结果对比的当前口径
来自 `h13d2_fixcheck_after_agc_bugfix_vs_old_summary.csv`：
- 对已有旧结果可对齐的两组参数点（`kp005_ki00625`、`kp02_ki005`），old-vs-new 指标差异较小；
- `min_hz`、`max_hz`、`abs_mean_hz`、`final_hz` 均只有小幅变化；
- 当前证据支持：**这次 AGC bugfix 没有把 h13d2 在已对齐参数点上的频率响应改成另一种完全不同的行为模式。**

## 与根因主任务的关系
- `sim-20260314-1048-rootcause-drilldown` 的主任务已经收口到模型硬边界，结论载于：
  - `/Users/hhuhzl/.openclaw/workspace-sim/deadband/results/20260315_0025_h13d2_rootcause_bus_esd1_fixed/final_hard_boundary_conclusion.md`
- 本次 `fixcheck` 不是继续上钻设备级根因，而是**bugfix 后的后续验证任务**；
- 因此应与根因 drilldown 主 job 分账，不应继续挂在旧 rootcause job 的 `running` 状态下。

## 当前建议口径
- 根因主任务：按“模型硬边界收口”管理；
- fixcheck 任务：按“后续验证/对比复核”单独建 job 管理；
- 对外汇报时，不要把 fixcheck 误写成根因 drilldown 的继续产物。