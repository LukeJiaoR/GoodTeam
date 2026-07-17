import { createElement, useState } from 'react';
import DirectoryPickerModal from '../components/DirectoryPickerModal';
import { chooseLocalPath as chooseLocalPathWithFallback } from '../utils/localPathPicker';

export function useDirectoryPicker(defaultOnSelectDirectory, onError, options = {}) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [pickerInitialPath, setPickerInitialPath] = useState('');
  const [pickerOnSelect, setPickerOnSelect] = useState(() => defaultOnSelectDirectory);
  const [pickerOptions, setPickerOptions] = useState({ mode: 'directory' });

  const openDirectoryPicker = (currentPath, onSelectDirectory = defaultOnSelectDirectory, options = {}) => {
    setPickerInitialPath(currentPath || '');
    setPickerOnSelect(() => onSelectDirectory);
    setPickerOptions({
      mode: options.mode || 'directory',
      strings: options.strings,
    });
    setPickerOpen(true);
  };

  const chooseLocalPath = ({
    mode = 'directory',
    initialPath = '',
    onSelected = defaultOnSelectDirectory,
    fetchImpl,
  }) => chooseLocalPathWithFallback({
    mode,
    initialPath,
    onSelected,
    openWebPicker: () => openDirectoryPicker(initialPath, onSelected, {
      mode,
      strings: options.strings,
    }),
    onError,
    fetchImpl,
  });

  const directoryPickerModal =
    pickerOpen &&
    createElement(DirectoryPickerModal, {
      initialPath: pickerInitialPath,
      mode: pickerOptions.mode,
      strings: pickerOptions.strings,
      onClose: () => setPickerOpen(false),
      onSelect: pickerOnSelect,
    });

  return {
    chooseLocalPath,
    directoryPickerModal,
    openDirectoryPicker,
  };
}
