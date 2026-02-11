import { describe, expect, it } from "vitest";

import { extractCsvFiles, removeUploadedFileByName } from "./csvImportPanel";


describe("csvImportPanel helpers", () => {
  it("filters csv files", () => {
    const csv = new File(["a,b\n1,2\n"], "data.csv", { type: "text/csv" });
    const txt = new File(["hello"], "notes.txt", { type: "text/plain" });
    const files = [csv, txt];

    const selected = extractCsvFiles({
      length: files.length,
      item: (index: number) => files[index] ?? null,
      0: files[0],
      1: files[1],
    } as unknown as FileList);

    expect(selected).toHaveLength(1);
    expect(selected[0].name).toBe("data.csv");
  });

  it("removes uploaded file by name", () => {
    const files = [
      new File(["a,b"], "a.csv"),
      new File(["a,b"], "b.csv"),
    ];
    const next = removeUploadedFileByName(files, "a.csv");
    expect(next.map((file) => file.name)).toEqual(["b.csv"]);
  });
});
