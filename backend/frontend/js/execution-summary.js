export function renderExecutionSummary(parent, stats) {
    let box = parent.querySelector('[data-execution-summary]');
    if (!box) {
        box = document.createElement('div');
        box.dataset.executionSummary = 'true';
        box.className = 'item-meta';
        parent.appendChild(box);
    }
    if (!stats) { box.textContent = '此历史复盘尚无行动统计，重新生成后可查看。'; return; }
    const mins = value => (value / 60).toFixed(1);
    const deviation = stats.deviation_seconds === null ? '暂无可比较记录' :
        `已完成步骤偏差 ${stats.deviation_seconds >= 0 ? '+' : ''}${mins(stats.deviation_seconds)} 分钟`;
    box.textContent = `记录投入 ${mins(stats.recorded_focus_seconds)} 分钟（含中止）；完成 ${stats.completed_steps} 步；${deviation}。` +
        `可比较 ${stats.comparable_steps} 步，缺少基准或计时 ${stats.unmeasured_steps} 步。投入归入番茄结束日；偏差使用已完成步骤的累计计时。`;
}
