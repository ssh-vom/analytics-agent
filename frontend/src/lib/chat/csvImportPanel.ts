export function extractCsvFiles(input: FileList | null): File[] {
  if (!input) {
    return [];
  }
  return Array.from(input).filter((file) => file.name.toLowerCase().endsWith(".csv"));
}

export function removeUploadedFileByName(files: File[], filename: string): File[] {
  return files.filter((file) => file.name !== filename);
}
