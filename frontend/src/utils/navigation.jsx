import {
  LayoutDashboard,
  Users,
  Activity,
  Kanban,
  PlaySquare,
  CheckCircle2,
  Settings,
  FileCode,
  MessageSquare,
  GitBranch,
  GitPullRequest,
  ClipboardCheck,
  BookOpen,
} from 'lucide-react';

export function buildProjectNavGroups(t) {
  return [
    {
      title: t('nav.groups.work'),
      items: [
        { id: 'overview', label: t('nav.tabs.overview'), icon: LayoutDashboard },
        { id: 'approvals', label: t('nav.tabs.approvals'), icon: CheckCircle2 },
        { id: 'workbench', label: t('nav.tabs.workbench'), icon: ClipboardCheck },
        { id: 'artifacts', label: t('nav.tabs.artifacts'), icon: FileCode },
        { id: 'actions', label: t('nav.tabs.actions'), icon: Activity },
        { id: 'board', label: t('nav.tabs.board'), icon: Kanban },
      ],
    },
    {
      title: t('nav.groups.observe'),
      items: [
        { id: 'workflow', label: t('nav.tabs.workflow'), icon: GitBranch },
        { id: 'devloop', label: t('nav.tabs.devloop'), icon: GitPullRequest },
        { id: 'messages', label: t('nav.tabs.messages'), icon: MessageSquare },
      ],
    },
    {
      title: t('nav.groups.inspect'),
      items: [
        { id: 'agents_memory', label: t('nav.tabs.agentsMemory'), icon: Users },
        { id: 'smoke', label: t('nav.tabs.smoke'), icon: PlaySquare },
      ],
    },
    {
      title: t('nav.groups.system'),
      items: [
        { id: 'system', label: t('nav.tabs.system'), icon: Settings },
        { id: 'docs', label: t('nav.tabs.docs'), icon: BookOpen },
      ],
    },
  ];
}
