import { act, fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ToastProvider, useToast } from './ToastProvider';

function TestComponent() {
  const toast = useToast();
  return (
    <div>
      <button onClick={() => toast.success('Settings saved successfully')}>Trigger Success</button>
      <button onClick={() => toast.error('Failed to save')}>Trigger Error</button>
      <button onClick={() => toast.info('New activity received')}>Trigger Info</button>
    </div>
  );
}

describe('ToastProvider', () => {
  it('renders children without crashing', () => {
    render(
      <ToastProvider>
        <div>Hello World</div>
      </ToastProvider>,
    );
    expect(screen.getByText('Hello World')).toBeTruthy();
  });

  it('displays toast when triggered and dismisses on close button click', async () => {
    render(
      <ToastProvider>
        <TestComponent />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText('Trigger Success'));
    expect(screen.getByText('Settings saved successfully')).toBeTruthy();

    const closeBtn = screen.getByLabelText('Close notification');
    fireEvent.click(closeBtn);
    expect(screen.queryByText('Settings saved successfully')).toBeNull();
  });

  it('auto dismisses toast after specified duration', async () => {
    vi.useFakeTimers();
    render(
      <ToastProvider>
        <TestComponent />
      </ToastProvider>,
    );

    act(() => {
      fireEvent.click(screen.getByText('Trigger Error'));
    });
    expect(screen.getByText('Failed to save')).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(4000);
    });
    expect(screen.queryByText('Failed to save')).toBeNull();
    vi.useRealTimers();
  });
});
