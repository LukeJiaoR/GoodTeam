import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import Breadcrumb from './Breadcrumb';

const mockT = (key) => {
  const dict = {
    'product.navigation.projects': '项目',
    'product.navigation.work': '工作',
    'product.navigation.review': '审查',
    'product.navigation.knowledge': '知识库',
    'product.navigation.projectSettings': '项目设置',
    'product.navigation.breadcrumb': '面包屑导航',
    'work.pipeline.takeover': '接管',
    'work.stage.prd_review': 'PRD 评审',
  };
  return dict[key] || key;
};

describe('Breadcrumb', () => {
  it('renders project work breadcrumb correctly', () => {
    render(
      <MemoryRouter initialEntries={['/projects/DEMO/work']}>
        <Breadcrumb projectKey="DEMO" t={mockT} />
      </MemoryRouter>,
    );

    expect(screen.getByText('项目')).toBeTruthy();
    expect(screen.getByText('DEMO')).toBeTruthy();
    expect(screen.getByText('工作')).toBeTruthy();
  });

  it('renders nested review item breadcrumb correctly', () => {
    render(
      <MemoryRouter initialEntries={['/projects/DEMO/review/prd_review']}>
        <Breadcrumb projectKey="DEMO" t={mockT} />
      </MemoryRouter>,
    );

    expect(screen.getByText('项目')).toBeTruthy();
    expect(screen.getByText('DEMO')).toBeTruthy();
    expect(screen.getByText('审查')).toBeTruthy();
    expect(screen.getByText('PRD 评审')).toBeTruthy();
  });
});
