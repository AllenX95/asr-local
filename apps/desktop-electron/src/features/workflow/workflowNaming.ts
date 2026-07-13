export function parseAudioBaseName(selectedPath: string): string {
  const file = selectedPath.split(/[\\/]/u).pop() || 'meeting'
  return file.replace(/\.[^.]+$/u, '') || 'meeting'
}

export function nextBaseNameForAudioSelection(currentName: string, previousSourcePath: string, selectedPath: string): string {
  const trimmed = currentName.trim()
  const previousAutoName = previousSourcePath ? parseAudioBaseName(previousSourcePath) : ''
  if (!trimmed || trimmed === 'meeting' || (previousAutoName && trimmed === previousAutoName)) {
    return parseAudioBaseName(selectedPath)
  }
  return currentName
}
