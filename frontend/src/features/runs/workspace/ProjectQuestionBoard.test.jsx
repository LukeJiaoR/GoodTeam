import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import { translate } from '../../../i18n';
import ProjectConversation from './ProjectConversation';

it('keeps current and historical questions visible and records an answer without closing the question', async () => {
  vi.stubGlobal('requestAnimationFrame', callback => callback());
  const question = { id: `human-${'a'.repeat(32)}`, continuation_id: 'current',
    question: '这次文档面向内部员工还是外部客户？', why_human: '目标读者需要业务取舍。',
    impact: '影响文档示例，独立健康检查可以继续。', rationale: '现有调查没有给出读者定位。',
    status: 'awaiting_answer', answer: null, resolution: null, current: true, can_answer: true, considered: false };
  const answered = { ...question, id: 'answered', question: '哪个团队负责 API？', status: 'answered',
    answer: { text: '内部支持团队。' }, can_answer: false, considered: true };
  const resolved = { ...answered, id: 'resolved', question: '是否保留旧客户端？', status: 'resolved',
    resolution: { status: 'resolved', reason: '按回答保留兼容入口，相关任务已经交付。' } };
  const notNeeded = { ...question, id: 'not-needed', question: '是否还要第三方导出？', status: 'not_needed',
    can_answer: false, resolution: { status: 'not_needed', reason: '目标调整后已不需要该能力。' } };
  const historical = { ...question, id: 'historical', continuation_id: 'earlier', question: '上一阶段是否扩大范围？',
    current: false, can_answer: false };
  const historicalAnswer = { ...answered, id: 'historical-answer', continuation_id: 'earlier',
    question: '上一阶段的受众是谁？', current: false };
  const onAnswer = vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce({ session: {} });
  render(<ProjectConversation projectKey="APP" questionBoard={{ schema_version: 'project_question_board.v1',
    items: [question, answered, resolved, notNeeded, historical, historicalAnswer] }}
    onAnswerQuestion={onAnswer} executionRunning mission={{ read_only: true, run_id: 'historical-run' }}
    assistantState={{}} timeline={[]} language="zh-CN" t={(key, params) => translate('zh-CN', key, params)} />);

  const waiting = screen.getByRole('region', { name: '待回答' });
  const answeredColumn = screen.getByRole('region', { name: '已答待处理' });
  const closed = screen.getByRole('region', { name: '已关闭' });
  expect(within(waiting).getByText('2')).toBeInTheDocument();
  expect(within(answeredColumn).getByText('2')).toBeInTheDocument();
  expect(within(closed).getByText('2')).toBeInTheDocument();
  expect(within(within(answeredColumn).getByRole('article', { name: answered.question }))
    .getByText('Manager 已将回答纳入判断，问题仍待处理。')).toBeInTheDocument();
  fireEvent.click(within(waiting).getByText('历史问题（1）'));
  const oldQuestion = within(waiting).getByRole('article', { name: historical.question });
  expect(within(oldQuestion).getByText('历史问题')).toBeInTheDocument();
  expect(within(oldQuestion).getByText(/Manager 仍可结合当前项目情况更新处理结论/)).toBeInTheDocument();
  expect(within(oldQuestion).queryByRole('textbox')).not.toBeInTheDocument();
  fireEvent.click(within(closed).getByText('查看已关闭问题（2）'));
  expect(within(closed).getByText('已解决')).toBeInTheDocument();
  expect(within(closed).getByText('不再需要')).toBeInTheDocument();
  expect(within(closed).getByText(resolved.resolution.reason)).toBeInTheDocument();
  expect(within(closed).getByText(notNeeded.resolution.reason)).toBeInTheDocument();

  const card = within(waiting).getByRole('article', { name: question.question });
  const input = within(card).getByRole('textbox', { name: '回答这个问题' });
  expect(input).toBeEnabled(); // Viewing an old run or an active Worker is not a project answer gate.
  expect(within(card).getByText(/影响文档示例/)).toBeInTheDocument();
  fireEvent.change(input, { target: { value: '只面向内部员工。' } });
  fireEvent.click(within(card).getByRole('button', { name: '提交回答' }));
  await waitFor(() => expect(within(card).getByRole('alert')).toHaveTextContent('提交尚未确认'));
  expect(input).toHaveValue('只面向内部员工。');
  fireEvent.click(within(card).getByRole('button', { name: '重试提交' }));
  await waitFor(() => expect(within(answeredColumn).getByRole('article', { name: question.question })).toBeInTheDocument());
  const recorded = within(answeredColumn).getByRole('article', { name: question.question });
  expect(within(recorded).getByText('只面向内部员工。')).toBeInTheDocument();
  expect(within(recorded).getByText('回答已记录，等待 Manager 接续处理。')).toBeInTheDocument();
  expect(within(closed).queryByRole('article', { name: question.question })).not.toBeInTheDocument();
  expect(onAnswer).toHaveBeenCalledTimes(2);
  expect(onAnswer).toHaveBeenLastCalledWith(question.id, '只面向内部员工。');
  expect(screen.queryByText(question.id)).not.toBeInTheDocument();
  localStorage.clear();
  vi.unstubAllGlobals();
});
