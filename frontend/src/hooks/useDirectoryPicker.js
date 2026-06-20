import { createElement, useState } from 'react';
import DirectoryPickerModal from '../components/DirectoryPickerModal';

export function useDirectoryPicker(defaultOnSelectDirectory) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerInitialPath, setPickerInitialPath] = useState('');
  const [pickerOnSelect, setPickerOnSelect] = useState(() => defaultOnSelectDirectory);
  const [pickerOptions, setPickerOptions] = useState({ mode: 'directory' });

  const openDirectoryPicker = (currentPath, onSelectDirectory = defaultOnSelectDirectory, options = {}) => {
    setPickerInitialPath(currentPath || '');
    setPickerOnSelect(() => onSelectDirectory);
    setPickerOptions({ mode: options.mode || 'directory' });
    setPickerOpen(true);
  };

  const directoryPickerModal =
    pickerOpen &&
    createElement(DirectoryPickerModal, {
      initialPath: pickerInitialPath,
      mode: pickerOptions.mode,
      onClose: () => setPickerOpen(false),
      onSelect: pickerOnSelect,
    });

  return { directoryPickerModal, openDirectoryPicker };
}
