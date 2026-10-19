from __future__ import annotations

import shutil
import subprocess

import pytest

from ai_harness.observability.dashboard import DASHBOARD_HTML


def test_history_cleanup_is_reversible_filtered_and_never_hides_live_work() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for dashboard interaction checks")
    version = subprocess.check_output([node, "--version"], text=True)
    if int(version.removeprefix("v").split(".")[0]) < 18:
        pytest.skip("Dashboard interaction checks require Node 18+")
    script = DASHBOARD_HTML.split("<script>", 1)[1].split("</script>", 1)[0]
    syntax = subprocess.run([node, "--check"], input=script, text=True, capture_output=True, timeout=10)
    assert syntax.returncode == 0, syntax.stderr
    helpers = script[script.index("function historyIdentity(task)"):script.index("function syncSelect(")]
    loader = script[script.index("function loadHiddenHistory()"):script.index("const $=id=>")]
    code = r"""
import assert from 'node:assert/strict';
const elements = new Map();
const $ = id => {if(!elements.has(id))elements.set(id,{});return elements.get(id)};
const historyStorageKey = 'agent-dashboard-hidden-history-v1';
let saved = '[]', storageFails = false, confirms = true, renders = 0;
const localStorage = {setItem(key,value){assert.equal(key,historyStorageKey);if(storageFails)throw new Error('quota');saved=value},getItem(){return saved}};
const messages = [], toast = (message,error=false) => messages.push({message,error});
const confirm = () => confirms;
const renderAttention = () => renders++;
const renderTasks = () => renders++;
const renderProjects = () => renders++;
const tasks = [
 {id:1,status:'completed',project:'a'}, {id:2,status:'completed',project:'b'},
 {id:3,status:'cancelled',project:'a'}, {id:4,status:'blocked',project:'a',run_id:'blocked-a',updated_at:10},
 {id:5,status:'failed',project:'b',run_id:'failed-b',updated_at:10},
 {id:6,status:'running',project:'a',run_id:'running'}, {id:7,status:'queued',project:'a'},
 {id:8,status:'blocked',project:'a',run_id:'inconsistent-running',updated_at:10},
];
const runs = [{run_id:'blocked-a',status:'blocked'}, {run_id:'failed-b',status:'failed'},
 {run_id:'running',status:'running'}, {run_id:'inconsistent-running',status:'running'},
 {run_id:'orphan-stopped',status:'blocked',project:'a',updated_at:10}];
const state = {data:{queue:{items:tasks},runs:{items:runs}},hiddenHistory:new Set(),filter:'all',project:'a',repository:'',branch:'',worker:''};
const runById = id => runs.find(run=>run.run_id===id);
const taskProjectIdentity = task => ({key:task.project||runById(task.run_id)?.project});
const taskStage = task => task.status;
""" + loader + helpers + r"""
updateHistoryControls(tasks);
assert.equal($('clearHistory').textContent,'Очистить историю (2)');
assert.equal($('hideStoppedHistory').textContent,'Скрыть остановленные (2)');
assert.equal($('restoreHistory').hidden,true);
clearHistory();
assert.equal(historyIsHidden(tasks[0]),true);
assert.equal(historyIsHidden(tasks[2]),true);
assert.equal(historyIsHidden(tasks[1]),false); // Other project remains visible.
assert.equal(historyIsHidden(tasks[3]),false); // Completed cleanup leaves stopped work alone.
assert.equal(historyIsHidden(tasks[5]),false);
assert.equal(historyIsHidden(tasks[6]),false);
assert.equal(historyIsHidden(tasks[7]),false); // Live run wins over stale blocked queue state.
state.hiddenHistory=loadHiddenHistory(); // Browser reload retains hidden rows.
assert.equal(historyIsHidden(tasks[0]),true);
confirms=false;hideStoppedHistory();assert.equal(runHistoryIsHidden(runs[0]),false);
confirms=true;hideStoppedHistory();assert.equal(runHistoryIsHidden(runs[0]),true);
assert.equal(runHistoryIsHidden(runs[1]),false); // Other project's attention still visible.
assert.equal(runHistoryIsHidden(runs[4]),true); // Legacy runs without queue rows also clean up.
assert.equal(runs[4].status,'blocked');
assert.equal(tasks[3].status,'blocked');assert.equal(runs[0].status,'blocked'); // No approval/cancellation.
const previousSaved=saved;
tasks[3].status='running';runs[0].status='running';assert.equal(historyIsHidden(tasks[3]),false);
tasks[3].status='blocked';runs[0].status='blocked';tasks[3].updated_at=20;
assert.equal(historyIsHidden(tasks[3]),false); // New failure resurfaces even if browser missed retry.
assert.equal(saved,previousSaved);
hideHistoryTask(tasks[1]);assert.equal(historyIsHidden(tasks[1]),true);
hideHistoryTask(tasks[5]);assert.equal(historyIsHidden(tasks[5]),false);
const before=[...state.hiddenHistory];storageFails=true;
restoreHistory();assert.deepEqual([...state.hiddenHistory],before);assert.equal(messages.at(-1).error,true);
storageFails=false;restoreHistory();assert.equal(state.hiddenHistory.size,0);assert.equal(saved,'[]');
assert.equal(tasks.some(historyIsHidden),false);assert.equal(runHistoryIsHidden(runs[4]),false);assert.ok(renders>0);
// Branch/status filters constrain bulk cleanup as well as project filters.
state.project='';state.filter='completed';state.branch='selected';tasks[1].payload={branch:'selected'};
assert.deepEqual(historyCandidates().map(task=>task.id),[2]);
clearHistory();assert.equal(historyIsHidden(tasks[1]),true);assert.equal(historyIsHidden(tasks[0]),false);
"""
    result = subprocess.run([node, "--input-type=module", "-"], input=code, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
