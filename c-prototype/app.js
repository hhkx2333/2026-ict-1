/* All user and model text is inserted via textContent, never interpreted as HTML. */
'use strict';
const $ = id => document.getElementById(id);
const state = {
  me: null,
  questions: [],
  rubrics: [],
  answers: [],
  cases: [],
  task: null,
  tab: 'answers',
  selected: new Set(),
  batchKey: null
};
const reasonLabels = {
  DRAFT_RUBRIC_REQUIRES_TEACHER_APPROVAL: '规则尚为草案，需要教师审核',
  EMPTY_ANSWER: '空答案，需要人工判断',
  MODEL_CALL_FAILED: '模型调用失败，未生成分数',
  INTERRUPTED_CALL_OUTCOME_UNKNOWN: '任务中断，调用结果未知，请核对后再明确重试',
  INVALID_GRADING_CONFIGURATION: '评分配置无效，请检查规则和案例版本',
  PROVIDER_CONFIGURATION_OR_EXECUTION_FAILED: '模型配置或执行失败，请检查服务端设置'
};
const labels = {
  RETRIED: '已创建重试',
  PENDING: '等待处理',
  RUNNING: '正在评分',
  COMPLETED: '已处理，待复核',
  FAILED: '调用失败',
  NEEDS_ATTENTION: '中断，需人工处理',
  IMPORTED: '已导入',
  SUGGESTED: '评分建议',
  REVIEW_REQUIRED: '需要复核'
};

function node(parent, tag, text, cls) {
  const n = document.createElement(tag);
  if (text !== undefined) n.textContent = text;
  if (cls) n.className = cls;
  parent.append(n);
  return n;
}

function button(parent, text, fn, cls) {
  const b = node(parent, 'button', text, cls);
  b.type = 'button';
  b.onclick = () => act(fn);
  return b;
}

function notice(text, error = false) {
  $('notice').textContent = text;
  $('notice').className = error ? 'error' : '';
}
async function api(path, body, method = 'POST') {
  const options = {
    credentials: 'same-origin',
    headers: {}
  };
  if (body !== undefined) {
    options.method = method;
    options.headers = {
      'Content-Type': 'application/json',
      'X-CSRF-Token': state.me?.csrf || ''
    };
    options.body = JSON.stringify(body);
  }
  const response = await fetch('/api/v1' + path, options);
  const value = await response.json();
  if (!response.ok) throw new Error(typeof value.detail === 'string' ? value.detail : '请求未完成。');
  return value;
}
async function act(fn) {
  try {
    await fn();
  } catch (error) {
    notice(error.message, true);
  }
}

function course() {
  return Number($('course').value);
}

function question() {
  return state.questions.find(q => q.id === Number($('question').value));
}

function rubric() {
  return state.rubrics.find(r => r.id === Number($('batch-rubric').value));
}

function option(select, value, text) {
  const o = node(select, 'option', text);
  o.value = value;
}

function jsonDetails(parent, title, data) {
  const d = node(parent, 'details');
  node(d, 'summary', title);
  node(d, 'pre', JSON.stringify(data, null, 2));
}

function formData(form) {
  return Object.fromEntries(new FormData(form));
}

function wireForm(id, fn) {
  $(id).onsubmit = e => {
    e.preventDefault();
    act(() => fn(formData(e.currentTarget), e.currentTarget));
  };
}

function setTab(tab) {
  state.tab = tab;
  for (const n of $('tabs').children) n.classList.toggle('active', n.dataset.tab === tab);
  for (const name of ['answers', 'rules', 'batches', 'practices', 'members']) $('tab-' + name).hidden = name !== tab;
}
for (const n of $('tabs').children) n.onclick = () => setTab(n.dataset.tab);
async function enter() {
  state.me = await api('/me');
  $('login-panel').hidden = true;
  $('workspace').hidden = false;
  $('identity').textContent = state.me.username + ' · ' + ({
    admin: '管理员',
    teacher: '教师',
    student: '学生'
  } [state.me.role]);
  $('teacher').hidden = state.me.role === 'student';
  $('student').hidden = state.me.role !== 'student';
  $('admin-panel').hidden = state.me.role !== 'admin';
  if (state.me.role === 'student') return loadStudent();
  await loadCourses();
}
wireForm('login-form', async (body, form) => {
  state.me = await api('/login', body);
  form.reset();
  await enter();
  notice('已登录。');
});
$('logout').onclick = () => act(async () => {
  await api('/logout', {});
  location.reload();
});
async function loadCourses() {
  const old = $('course').value;
  const rows = await api('/courses');
  $('course').replaceChildren();
  for (const r of rows) option($('course'), r.id, r.name);
  if (rows.some(r => String(r.id) === old)) $('course').value = old;
  await loadQuestions();
}
async function loadQuestions() {
  state.selected.clear();
  state.batchKey = null;
  const old = $('question').value;
  state.questions = course() ? await api(`/courses/${course()}/questions`) : [];
  $('question').replaceChildren();
  for (const q of state.questions) option($('question'), q.id, q.data.question_id + ' · ' + (q.synthetic ? '合成演示' : q.data.text.slice(0, 26)));
  if (state.questions.some(q => String(q.id) === old)) $('question').value = old;
  await loadQuestion();
}
async function loadQuestion() {
  state.selected.clear();
  state.batchKey = null;
  const q = question();
  $('question-info').replaceChildren();
  if (!q) {
    node($('question-info'), 'p', '暂无题目。可以新建题目，或按 README 导入 A 数据与合成演示。', 'muted');
    state.rubrics = [];
    state.answers = [];
    state.cases = [];
    renderAnswers();
    $('batch-rubric').replaceChildren();
    $('rule-list').replaceChildren();
    $('draft-list').replaceChildren();
  } else {
    node($('question-info'), 'span', q.synthetic ? '合成演示 · 不作效果证据' : '题目 ' + q.data.question_id, 'badge');
    node($('question-info'), 'h2', q.data.text);
    node($('question-info'), 'p', `满分 ${q.data.max_score} · ${q.current_rubric_id?'已有发布规则':'暂无教师发布规则'}`, 'muted');
    jsonDetails($('question-info'), '查看来源与版本', q.source);
    [state.rubrics, state.answers, state.cases] = await Promise.all([api(`/questions/${q.id}/rubrics`), api(`/questions/${q.id}/answers`), api(`/questions/${q.id}/case-sets`)]);
    const old = $('batch-rubric').value;
    $('batch-rubric').replaceChildren();
    for (const r of state.rubrics) option($('batch-rubric'), r.id, r.data.version + ' · ' + r.data.status);
    if (state.rubrics.some(r => String(r.id) === old)) $('batch-rubric').value = old;
    else if (q.current_rubric_id) $('batch-rubric').value = q.current_rubric_id;
    $('provider').value = q.synthetic ? 'synthetic' : 'live';
    $('allow-draft').checked = rubric()?.data.status === 'DRAFT';
    renderRules();
    renderAnswers();
    const drafts = await api(`/questions/${q.id}/drafts`);
    $('draft-list').replaceChildren();
    node($('draft-list'), 'p', `${drafts.length} 份草案，均不自动进入审核案例库。`, 'muted');
    for (const d of drafts) jsonDetails($('draft-list'), '草案 ' + d.id + ' · ' + (d.data.case_id || d.data.draft_id || ''), d);
  }
  if (course()) await Promise.all([loadBatches(), loadPractices(), loadMembers()]);
}

function renderRules() {
  renderRubricFields();
  $('rule-list').replaceChildren();
  for (const r of state.rubrics) {
    const div = node($('rule-list'), 'div', undefined, 'record');
    node(div, 'p', r.data.version + ' · ' + r.data.status);
    if (r.published_at) node(div, 'p', `审核账号 ${r.published_by} · ${r.published_at}`, 'small muted');
    button(div, '选择此版本', async () => {
      $('batch-rubric').value = r.id;
      renderRules();
    });
  }
  renderCases();
}

function renderCases() {
  $('case-list').replaceChildren();
  $('batch-cases').replaceChildren();
  option($('batch-cases'), '', '不使用案例');
  for (const c of state.cases) {
    node($('case-list'), 'p', `${c.version} · ${c.count} 条 · 规则编号 ${c.rubric_id}`);
    if (c.rubric_id === rubric()?.id) option($('batch-cases'), c.id, c.version);
  }
}
$('batch-rubric').onchange = () => {
  state.batchKey = null;
  renderRules();
};

function renderAnswers() {
  $('answers').replaceChildren();
  $('answer-count').textContent = `${state.answers.length} 份 · 已选 ${state.selected.size} 份`;
  for (const a of state.answers) {
    const card = node($('answers'), 'article');
    const row = node(card, 'div', undefined, 'row');
    const label = node(row, 'label', undefined, 'check');
    const check = node(label, 'input');
    check.type = 'checkbox';
    check.checked = state.selected.has(a.id);
    check.onchange = () => {
      check.checked ? state.selected.add(a.id) : state.selected.delete(a.id);
      state.batchKey = null;
      $('answer-count').textContent = `${state.answers.length} 份 · 已选 ${state.selected.size} 份`;
    };
    node(label, 'strong', a.answer_id);
    node(row, 'span', a.review_id ? '已复核' : labels[a.status], `badge ${['FAILED','NEEDS_ATTENTION'].includes(a.status)?'danger':''}`);
    if (a.stale) node(card, 'p', '规则已更新，旧结果需要重审。', 'badge warning');
    node(card, 'p', a.text || '（空答案）', 'answer-text');
    if (a.suggested_status) node(card, 'span', labels[a.suggested_status] || a.suggested_status, 'badge');
    const controls = node(card, 'div', undefined, 'controls');
    if (a.latest_task_id) button(controls, '查看建议与复核', () => openTask(a.latest_task_id));
    button(controls, '分配给学生', async () => {
      const value = prompt('输入本课程学生的用户编号');
      if (value === null) return;
      await api(`/answers/${a.id}/assign`, {
        user_id: Number(value)
      });
      notice('答案已分配。');
      await loadQuestion();
    });
    if (a.student_id) node(card, 'p', '学生编号 ' + a.student_id, 'small muted');
  }
}
async function loadBatches() {
  const rows = await api(`/courses/${course()}/batches`);
  $('batch-list').replaceChildren();
  if (!rows.length) node($('batch-list'), 'p', '还没有评分任务。', 'muted');
  for (const b of rows) {
    const d = node($('batch-list'), 'div', undefined, 'record');
    node(d, 'h3', '批次 ' + b.id);
    node(d, 'p', Object.entries(b.counts).map(([key, value]) => (labels[key] || key) + ' ' + value).join(' · '));
    const c = node(d, 'div', undefined, 'controls');
    for (const id of b.task_ids) button(c, '任务 ' + id, () => openTask(id));
  }
}
async function loadPractices() {
  const rows = await api(`/courses/${course()}/practices`);
  $('practice-list').replaceChildren();
  for (const p of rows) {
    const d = node($('practice-list'), 'div', undefined, 'record');
    node(d, 'p', p.data.practice_id + ' · ' + (p.approved ? '已审核' : '草案'));
    jsonDetails(d, '查看题目', p.data);
    if (!p.approved) button(d, '审核通过', async () => {
      await api(`/practices/${p.id}/approve`, {});
      await loadPractices();
    });
  }
  $('practice-criteria').replaceChildren();
  for (const r of rubric()?.data.criteria || []) option($('practice-criteria'), r.criterion_id, r.description);
}
async function loadMembers() {
  const rows = await api(`/courses/${course()}/members`);
  $('member-list').replaceChildren();
  for (const u of rows) node($('member-list'), 'p', `${u.id} · ${u.username} · ${u.role}`);
  const legacy = await api(`/courses/${course()}/legacy`);
  $('legacy-list').replaceChildren();
  for (const r of legacy) jsonDetails($('legacy-list'), r.data.original.answer_id + ' · 身份未核验', r.data);
  if (state.me.role === 'admin') {
    const users = await api('/users');
    $('user-list').replaceChildren();
    for (const u of users) node($('user-list'), 'p', `${u.id} · ${u.username} · ${u.role}`);
  }
}
$('course').onchange = () => act(loadQuestions);
$('question').onchange = () => act(loadQuestion);
$('refresh').onclick = () => act(loadQuestion);
$('new-course').onclick = () => act(async () => {
  const name = prompt('课程名称');
  if (!name) return;
  const c = await api('/courses', {
    name
  });
  await loadCourses();
  $('course').value = c.id;
  await loadQuestions();
});
async function questionDialog(existing = null) {
  if (!course()) throw new Error('请先新建课程。');
  const box = $('detail-body');
  box.replaceChildren();
  $('detail-title').textContent = existing ? '编辑题目' : '新建题目';
  const form = node(box, 'form');
  const fields = {};
  for (const [key, label, type] of [
      ['question_id', '题号', 'input'],
      ['text', '题目正文', 'textarea'],
      ['max_score', '满分', 'input'],
      ['reference_answer', '参考答案', 'textarea']
    ]) {
    const el = node(node(form, 'label', label), type);
    el.required = key !== 'reference_answer';
    if (existing) el.value = existing.data[key] ?? '';
    if (existing && key === 'question_id') el.readOnly = true;
    if (key === 'max_score') {
      el.type = 'number';
      el.min = 0.1;
      el.step = 'any';
    }
    fields[key] = el;
  }
  node(form, 'button', '保存题目', 'primary');
  form.onsubmit = e => {
    e.preventDefault();
    act(async () => {
      const data = Object.fromEntries(Object.entries(fields).map(([key, el]) => [key, key === 'max_score' ? Number(el.value) : el.value]));
      await api(existing ? `/questions/${existing.id}` : `/courses/${course()}/questions`, {
        data
      }, existing ? 'PATCH' : 'POST');
      $('detail').close();
      await loadQuestions();
      notice('题目已保存。请到规则与案例中添加得分点。');
    });
  };
  $('detail').showModal();
}
$('new-question').onclick = () => act(() => questionDialog());
$('edit-question').onclick = () => act(async () => {
  if (!question()) throw new Error('请选择题目。');
  await questionDialog(question());
});
$('import').onclick = () => act(async () => {
  const q = question(),
    file = $('answer-file').files[0];
  if (!q || !file) throw new Error('请选择题目和无分 JSONL 文件。');
  if (file.size > 4000000) throw new Error('文件须小于 4 MB。');
  const result = await api(`/questions/${q.id}/answers/import`, {
    text: await file.text()
  });
  notice(`导入 ${result.added} 份，跳过 ${result.existing} 份同文同编号答案。`);
  await loadQuestion();
});
$('select-all').onclick = () => {
  state.selected = new Set(state.answers.map(a => a.id));
  state.batchKey = null;
  renderAnswers();
};
$('run-batch').onclick = () => act(async () => {
  if (!rubric() || !state.selected.size) throw new Error('请选择规则版本和答案。');
  const body = {
    answer_ids: [...state.selected],
    rubric_id: rubric().id,
    mode: $('grade-mode').value,
    provider: $('provider').value,
    idempotency_key: state.batchKey || (state.batchKey = crypto.randomUUID()),
    case_set_id: $('batch-cases').value ? Number($('batch-cases').value) : null,
    allow_draft: $('allow-draft').checked,
    acknowledge_external_processing: $('external-ack').checked
  };
  const b = await api(`/courses/${course()}/batches`, body);
  notice(`批次 ${b.id} 已创建，请保持后台 Worker 运行并刷新进度。`);
  await loadQuestion();
  setTab('batches');
});
for (const id of ['grade-mode', 'provider', 'batch-cases', 'allow-draft', 'external-ack']) $(id).addEventListener('change', () => {
  state.batchKey = null;
});
for (const [id, trial] of [
    ['export', false],
    ['export-trial', true]
  ]) $(id).onclick = () => act(async () => {
  const response = await fetch(`/api/v1/courses/${course()}/export.csv?trial=${trial}`);
  if (!response.ok) throw new Error('导出失败，请检查登录状态。');
  const blob = await response.blob(),
    url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = trial ? '试验记录.csv' : '教师确认成绩.csv';
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
  notice(trial ? '已导出带标识的试验记录。' : '已导出当前规则下的教师确认成绩。');
});
$('save-rubric').onclick = () => act(async () => {
  if (!question()) throw new Error('请先选择题目。');
  await api(`/questions/${question().id}/rubrics`, {
    data: readRubricFields()
  });
  notice('新草案已保存。');
  await loadQuestion();
});
$('publish-rubric').onclick = () => act(async () => {
  if (!rubric()) throw new Error('请选择草案。');
  const version = prompt('实际审核后发布：请输入新的正式版本名');
  if (!version) return;
  const note = prompt('请输入本次实际审核说明（不会代替教师完成审核）');
  if (!note) return;
  await api(`/rubrics/${rubric().id}/publish`, {
    version,
    note
  });
  notice('已创建发布版本，旧结果保留并提示重审。');
  await loadQuestion();
});
wireForm('case-form', async (body, form) => {
  if (!rubric()) throw new Error('请选择已发布规则。');
  await api(`/rubrics/${rubric().id}/case-sets`, {
    review_ids: body.review_ids.split(',').map(s => Number(s.trim())),
    version: body.version
  });
  form.reset();
  notice('案例复用已单独批准。');
  await loadQuestion();
});
wireForm('member-form', async (body, form) => {
  await api(`/courses/${course()}/members`, {
    user_id: Number(body.user_id)
  });
  form.reset();
  await loadMembers();
  notice('成员已加入。');
});
wireForm('user-form', async (body, form) => {
  await api('/users', body);
  form.reset();
  await loadMembers();
  notice('账号已创建，请通过安全渠道交付初始密码。');
});
wireForm('practice-form', async (body, form) => {
  if (!question()) throw new Error('请选择来源题目。');
  const data = {
    practice_id: body.practice_id,
    question_id: body.question_id,
    source_question_id: question().data.question_id,
    criterion_ids: Array.from($('practice-criteria').selectedOptions).map(o => o.value),
    text: body.text,
    reference_answer: body.reference_answer
  };
  await api(`/courses/${course()}/practices`, {
    data
  });
  form.reset();
  await loadPractices();
  notice('练习草案已保存，审核后参与推荐。');
});
async function openTask(id) {
  const t = await api(`/tasks/${id}`);
  state.task = t;
  $('detail-title').textContent = t.request.answer.answer_id + ' · ' + labels[t.status];
  const container = $('detail-body');
  container.replaceChildren();
  node(container, 'p', `任务 ${t.id} · 规则 ${t.request.rubric.version} · ${t.provider==='synthetic'?'合成回放':'模型调用'}`, 'muted');
  const source = node(node(container, 'label', '原始答案（选中正文后可引用到得分点）'), 'textarea');
  source.value = t.request.answer.text;
  source.readOnly = true;
  source.rows = 6;
  if (t.stale) node(container, 'p', '规则已更新，请按新版本重评。', 'badge warning');
  if (t.error) node(container, 'p', reasonLabels[t.error] || '任务未能完成，请检查服务配置或重试。', 'badge danger');
  if (t.result) {
    node(container, 'h3', `建议分 ${t.result.suggested_total??'无'} / ${t.request.question.max_score}`);
    node(container, 'p', (t.result.review_reasons || []).map(r => reasonLabels[r] || r).join(' · '), 'muted');
    for (const item of t.result.items) {
      const d = node(container, 'div', undefined, 'criterion');
      node(d, 'strong', `${item.criterion_id} · 建议 ${item.suggested_score} 分`);
      node(d, 'p', item.reason);
      for (const e of item.evidence) node(d, 'p', `「${Array.from(t.request.answer.text).slice(e.start,e.end).join('')}」`, 'evidence');
    }
    jsonDetails(container, '调用与版本记录', t.result.metadata);
  }
  if (['FAILED', 'NEEDS_ATTENTION'].includes(t.status)) button(container, '显式重试此任务', async () => {
    if (t.provider === 'live' && !confirm('此次重试会再次向模型服务发送答案，可能重复计费。确认重试？')) return;
    const r = await api(`/tasks/${t.id}/retry`, {
      acknowledge_external_processing: t.provider === 'live'
    });
    $('detail').close();
    notice('已创建重试任务 ' + r.id);
    await loadQuestion();
  });
  if (t.review) {
    node(container, 'h3', (t.review.trial_only ? '试验复核分 ' : '教师确认分 ') + t.review.confirmed.confirmed_total);
    node(container, 'p', '复核记录编号 ' + t.review.id, 'badge');
    jsonDetails(container, '逐项确认记录', t.review.confirmed);
    if (!t.review.trial_only) {
      button(container, t.review.released ? '撤回学生端发布' : '发布给已分配学生', async () => {
        await api(`/reviews/${t.review.id}/release`, {
          release: !t.review.released
        });
        await openTask(t.id);
      });
      button(container, '查看反馈与练习', async () => jsonDetails(container, '反馈与练习', await api(`/reviews/${t.review.id}/feedback`)));
    }
  } else if (t.status === 'COMPLETED' && !t.stale) {
    const form = node(container, 'form');
    const fields = [];
    for (const c of t.request.rubric.criteria) {
      const initial = t.result?.items.find(i => i.criterion_id === c.criterion_id);
      const d = node(form, 'div', undefined, 'criterion');
      node(d, 'h3', c.description + ' · ' + c.criterion_id);
      const l = node(d, 'label', '确认分');
      const score = node(l, 'select');
      for (const v of c.allowed_scores) option(score, v, v);
      score.value = initial?.suggested_score ?? 0;
      const reason = node(node(d, 'label', '教师逐项理由'), 'textarea');
      reason.rows = 2;
      reason.required = true;
      reason.value = initial?.reason || '';
      const evidence = structuredClone(initial?.evidence || []);
      const evidenceBox = node(d, 'div');

      function showEvidence() {
        evidenceBox.replaceChildren();
        for (const [index, e] of evidence.entries()) {
          const quote = node(evidenceBox, 'div', undefined, 'evidence');
          node(quote, 'p', e.quote);
          button(quote, '移除此条引用', async () => {
            evidence.splice(index, 1);
            showEvidence();
          });
        }
      }
      showEvidence();
      button(d, '引用答案中选中的原文', async () => {
        const start = source.selectionStart,
          end = source.selectionEnd;
        if (start === end) throw new Error('请先在上方原始答案中选中一段依据。');
        evidence.push({
          quote: source.value.slice(start, end),
          start: Array.from(source.value.slice(0, start)).length,
          end: Array.from(source.value.slice(0, end)).length
        });
        showEvidence();
      });
      const rule = node(node(d, 'label', '规则编号'), 'select');
      for (const [key, value] of Object.entries(c.rules)) option(rule, key, key + ' · ' + value);
      if (initial) rule.value = initial.rule_id;
      fields.push({
        c,
        score,
        reason,
        evidence,
        rule
      });
    }
    const isTrial = t.provider === 'synthetic' || t.request.rubric.status === 'DRAFT';
    const release = node(node(form, 'label', undefined, 'check'), 'input');
    release.type = 'checkbox';
    release.disabled = isTrial;
    release.parentElement.append(document.createTextNode('确认后发布给学生（仅正式结果）'));
    const submit = node(form, 'button', isTrial ? '保存试验复核（不进入正式成绩）' : '保存教师逐项确认', 'primary');
    form.onsubmit = e => {
      e.preventDefault();
      act(async () => {
        submit.disabled = true;
        try {
          const items = fields.map(({
            c,
            score,
            reason,
            evidence,
            rule
          }) => ({
            criterion_id: c.criterion_id,
            score: Number(score.value),
            max_score: c.max_score,
            teacher_reason: reason.value,
            evidence: structuredClone(evidence),
            rule_id: rule.value
          }));
          await api(`/tasks/${t.id}/reviews`, {
            items,
            release: release.checked
          });
          notice('逐项复核已保存。');
          await openTask(t.id);
          await loadQuestion();
        } finally {
          submit.disabled = false;
        }
      });
    };
    button(container, '另存通用规则修改草案', async () => {
      const proposed_rule = prompt('填写供另行审核的通用规则建议，不会自动改变其他答案');
      if (!proposed_rule) return;
      const items = fields.map(({
        c,
        score,
        reason,
        evidence,
        rule
      }) => ({
        criterion_id: c.criterion_id,
        suggested_score: Number(score.value),
        reason: reason.value,
        evidence: structuredClone(evidence),
        rule_id: rule.value,
        flags: []
      }));
      await api(`/tasks/${t.id}/corrections`, {
        assessment: {
          items,
          review_reasons: []
        },
        scope: 'general_rule',
        proposed_rule
      });
      notice('已保存通用规则草案，尚未发布。');
    });
  }
  if (!$('detail').open) $('detail').showModal();
}
$('close-detail').onclick = () => $('detail').close();
async function loadStudent() {
  const rows = await api('/student/results');
  $('student-results').replaceChildren();
  if (!rows.length) node($('student-results'), 'p', '暂无已发布成绩。', 'muted');
  for (const r of rows) {
    const d = node($('student-results'), 'div', undefined, 'record');
    node(d, 'h3', r.question.question_id + ' · ' + r.confirmed.confirmed_total + ' / ' + r.question.max_score);
    node(d, 'p', r.question.text);
    node(d, 'p', r.answer, 'answer-text');
    jsonDetails(d, '本次作答反馈', r.learning.feedback);
    if (!r.learning.practice_candidates.length) node(d, 'p', '暂无待完成的审核练习。', 'muted');
    for (const p of r.learning.practice_candidates) {
      button(d, '开始练习 ' + p.practice_id, () => openPractice(p.record_id));
    }
  }
}
async function openPractice(id) {
  const p = await api('/student/practices/' + id);
  const body = $('detail-body');
  body.replaceChildren();
  $('detail-title').textContent = p.data.practice_id;
  node(body, 'p', p.data.text);
  if (p.attempt) {
    node(body, 'p', p.attempt, 'answer-text');
    node(body, 'p', '参考答案：' + p.data.reference_answer);
  } else {
    const answer = node(node(body, 'label', '我的答案'), 'textarea');
    answer.rows = 6;
    button(body, '提交练习', async () => {
      await api(`/student/practices/${p.id}/attempts`, {
        text: answer.value
      });
      $('detail').close();
      notice('练习已保存。');
      await openPractice(p.id);
      await loadStudent();
    });
  }
  if (!$('detail').open) $('detail').showModal();
}
$('student-refresh').onclick = () => act(loadStudent);


let rubricFields = [];

function inputField(parent, label, value, tag = 'input') {
  const e = node(node(parent, 'label', label), tag);
  e.value = value ?? '';
  return e;
}

function renderRubricFields() {
  rubricFields = [];
  $('rubric-fields').replaceChildren();
  $('rubric-version').value = rubric() ? rubric().data.version + '-draft-next' : (question()?.data.question_id || 'question') + '-draft-v1';
  for (const c of rubric()?.data.criteria || []) addCriterionField(structuredClone(c));
}

function addCriterionField(c) {
  const d = node($('rubric-fields'), 'div', undefined, 'criterion');
  node(d, 'p', '得分点 ' + c.criterion_id, 'small muted');
  const desc = inputField(d, '得分点名称', c.description);
  const max = inputField(d, '最高分', c.max_score);
  max.type = 'number';
  max.step = 'any';
  const levels = inputField(d, '允许分档（逗号分隔）', c.allowed_scores.join(', '));
  const rules = Object.entries(c.rules).map(([key, value]) => ({
    key,
    el: inputField(d, '评分口径', value, 'textarea')
  }));
  const checks = (c.checks || []).map(check => {
    const box = node(d, 'details');
    node(box, 'summary', '检查项 ' + check.check_id);
    return {
      original: check,
      desc: inputField(box, '检查内容', check.description, 'textarea'),
      max: inputField(box, '检查项最高分', check.max_score),
      levels: inputField(box, '检查项分档', check.allowed_scores.join(', '))
    };
  });
  const field = {
    original: c,
    desc,
    max,
    levels,
    rules,
    checks,
    removed: false
  };
  rubricFields.push(field);
  button(d, '移除此得分点', async () => {
    field.removed = true;
    d.remove();
  });
}

function readRubricFields() {
  if (!question()) throw new Error('请先选择题目。');
  const r = rubric() ? structuredClone(rubric().data) : {
    question_id: question().data.question_id
  };
  r.version = $('rubric-version').value;
  r.status = 'DRAFT';
  r.criteria = rubricFields.filter(f => !f.removed).map(f => {
    const c = {
      ...f.original,
      description: f.desc.value,
      max_score: Number(f.max.value),
      allowed_scores: f.levels.value.split(',').map(v => Number(v.trim())),
      rules: Object.fromEntries(f.rules.map(r => [r.key, r.el.value]))
    };
    if (f.checks.length) c.checks = f.checks.map(k => ({
      ...k.original,
      description: k.desc.value,
      max_score: Number(k.max.value),
      allowed_scores: k.levels.value.split(',').map(v => Number(v.trim()))
    }));
    return c;
  });
  return r;
}
$('add-criterion').onclick = () => act(async () => {
  if (!question()) throw new Error('请先选择题目。');
  const id = question().data.question_id + '-P' + (rubricFields.length + 1);
  addCriterionField({
    criterion_id: id,
    description: '新得分点',
    max_score: 1,
    allowed_scores: [0, 1],
    rules: {
      [id + '-policy']: '请填写判分口径'
    }
  });
});

enter().catch(() => {
  $('login-panel').hidden = false;
  $('workspace').hidden = true;
});
