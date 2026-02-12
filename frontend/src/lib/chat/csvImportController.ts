import { importCSV, fetchWorldlineTables } from "$lib/api/client";
import { extractCsvFiles, removeUploadedFileByName } from "$lib/chat/csvImportPanel";

export type CSVImportContext = {
  uploadedFiles: File[];
  setUploadedFiles: (files: File[]) => void;
  setStatusText: (text: string) => void;
  setSelectedContextTables: (tables: string[] | ((prev: string[]) => string[])) => void;
  setWorldlineTables: (tables: unknown) => void;
  activeWorldlineId: string;
  removeUploadedFile: (filename: string) => void;
};

export function createCSVImportController(context: CSVImportContext) {
  const {
    setUploadedFiles,
    setStatusText,
    setSelectedContextTables,
    setWorldlineTables,
    removeUploadedFile,
  } = context;

  // Access via context getters to get current values
  function getUploadedFiles(): File[] {
    return context.uploadedFiles;
  }

  function getActiveWorldlineId(): string {
    return context.activeWorldlineId;
  }

  function handleFileSelect(event: Event): void {
    const input = event.target as HTMLInputElement;
    const csvFiles = extractCsvFiles(input.files);
    if (csvFiles.length === 0) {
      return;
    }
    const currentFiles = getUploadedFiles();
    setUploadedFiles([...currentFiles, ...csvFiles]);
    setStatusText(
      csvFiles.length === 1
        ? `Attached ${csvFiles[0].name}. It will import when you send.`
        : `Attached ${csvFiles.length} CSV files. They will import when you send.`
    );
    input.value = "";
  }

  async function runCSVImport(
    worldlineId: string,
    file: File,
  ): Promise<{ table_name: string; row_count: number }> {
    try {
      const result = await importCSV(worldlineId, file);
      removeUploadedFile(file.name);

      if (getActiveWorldlineId() === worldlineId) {
        setSelectedContextTables((prev) =>
          [...new Set([...prev, result.table_name])]
        );
      }

      setStatusText(
        `Imported ${result.row_count} rows from ${file.name} into ${result.table_name}`
      );
      return result;
    } catch (error) {
      const detail = error instanceof Error ? error.message : "Import failed";
      setStatusText(`Import failed: ${detail}`);
      throw error instanceof Error ? error : new Error(detail);
    }
  }

  async function importUploadedFilesBeforeSend(
    worldlineId: string,
  ): Promise<void> {
    const uploadedFiles = getUploadedFiles();
    if (uploadedFiles.length === 0) {
      return;
    }

    const filesToImport = [...uploadedFiles];
    setStatusText(
      filesToImport.length === 1
        ? `Importing ${filesToImport[0].name}...`
        : `Importing ${filesToImport.length} files...`
    );

    for (const file of filesToImport) {
      await runCSVImport(worldlineId, file);
    }

    const tables = await fetchWorldlineTables(worldlineId);
    setWorldlineTables(tables);
  }

  return {
    handleFileSelect,
    runCSVImport,
    importUploadedFilesBeforeSend,
  };
}

export type CSVImportController = ReturnType<typeof createCSVImportController>;
