import { useState } from 'react';

export function useDispatchTrackForm() {
  const [dispatchName, setDispatchName] = useState('');
  const [dispatchRole, setDispatchRole] = useState('backend');
  const [dispatchCustomRole, setDispatchCustomRole] = useState('');
  const [dispatchAdapter, setDispatchAdapter] = useState('codex');
  const [dispatchCustomAdapter, setDispatchCustomAdapter] = useState('');
  const [dispatchTask, setDispatchTask] = useState('');
  const [dispatchConstraints, setDispatchConstraints] = useState('');
  const [dispatchVerify, setDispatchVerify] = useState('');
  const [dispatchLoading, setDispatchLoading] = useState(false);
  const [dispatchError, setDispatchError] = useState(null);
  const [dispatchSuccess, setDispatchSuccess] = useState(null);

  const resetDispatchForm = () => {
    setDispatchName('');
    setDispatchTask('');
    setDispatchConstraints('');
    setDispatchVerify('');
    setDispatchCustomRole('');
    setDispatchCustomAdapter('');
  };

  return {
    dispatchName,
    setDispatchName,
    dispatchRole,
    setDispatchRole,
    dispatchCustomRole,
    setDispatchCustomRole,
    dispatchAdapter,
    setDispatchAdapter,
    dispatchCustomAdapter,
    setDispatchCustomAdapter,
    dispatchTask,
    setDispatchTask,
    dispatchConstraints,
    setDispatchConstraints,
    dispatchVerify,
    setDispatchVerify,
    dispatchLoading,
    setDispatchLoading,
    dispatchError,
    setDispatchError,
    dispatchSuccess,
    setDispatchSuccess,
    resetDispatchForm,
  };
}
