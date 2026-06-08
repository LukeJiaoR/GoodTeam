import { createElement, useState } from 'react';
import DirectoryPickerModal from '../components/DirectoryPickerModal';

export function useDirectoryPicker(onSelectDirectory) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerInitialPath, setPickerInitialPath] = useState('');

  const openDirectoryPicker = (currentPath) => {
    setPickerInitialPath(currentPath || '');
    setPickerOpen(true);
  };

  const directoryPickerModal =
    pickerOpen &&
    createElement(DirectoryPickerModal, {
      initialPath: pickerInitialPath,
      onClose: () => setPickerOpen(false),
      onSelect: onSelectDirectory,
    });

  return { directoryPickerModal, openDirectoryPicker };
}
