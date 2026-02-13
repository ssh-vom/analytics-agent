# Code Metrics Report - Analytics Agent Repository

**Report Generated:** 2026-02-12

---

## Executive Summary

This repository contains **24,118 lines of code** across 121 files, with a total of **28,969 lines** including blank lines and comments. The codebase is split between a Python backend and a TypeScript/Svelte frontend.

### Overall Statistics
- **Total Files:** 121
- **Total Lines of Code:** 24,118
- **Blank Lines:** 3,342
- **Comment Lines:** 1,509
- **Languages:** 13 different languages

---

## 1. Lines of Code by Language

### Language Breakdown

| Language       | Files | Blank Lines | Comments | Code Lines | % of Total |
|---------------|-------|-------------|----------|------------|------------|
| Python        | 51    | 1,759       | 1,398    | 10,253     | 42.5%      |
| Svelte        | 16    | 924         | 8        | 7,370      | 30.6%      |
| TypeScript    | 29    | 382         | 33       | 2,843      | 11.8%      |
| JSON          | 3     | 0           | 0        | 2,702      | 11.2%      |
| Markdown      | 12    | 169         | 0        | 430        | 1.8%       |
| CSS           | 3     | 89          | 60       | 426        | 1.8%       |
| Bourne Shell  | 1     | 9           | 5        | 32         | 0.1%       |
| TOML          | 1     | 1           | 0        | 18         | 0.1%       |
| make          | 1     | 4           | 5        | 13         | 0.1%       |
| HTML          | 1     | 0           | 0        | 11         | 0.0%       |
| Dockerfile    | 1     | 3           | 0        | 10         | 0.0%       |
| JavaScript    | 1     | 2           | 0        | 9          | 0.0%       |
| Text          | 1     | 0           | 0        | 1          | 0.0%       |

### Top 3 Languages
1. **Python** - 10,253 lines (42.5%)
2. **Svelte** - 7,370 lines (30.6%)
3. **TypeScript** - 2,843 lines (11.8%)

---

## 2. Functional Code vs Tests

### Backend (Python)

| Category          | Files | Blank Lines | Comments | Code Lines | % of Backend |
|------------------|-------|-------------|----------|------------|--------------|
| **Functional Code** | 29    | 1,121       | 1,104    | 5,932      | 58.0%        |
| **Tests**           | 20    | 634         | 294      | 4,302      | 42.0%        |
| **Documentation**   | 7     | 149         | 0        | 385        | —            |
| **Total Backend**   | 51    | 1,759       | 1,398    | 10,253     | 100%         |

**Test Coverage Ratio:** 0.73:1 (4,302 test lines for 5,932 functional lines)

### Frontend (TypeScript/JavaScript)

| Category          | Files | Blank Lines | Comments | Code Lines | % of Frontend |
|------------------|-------|-------------|----------|------------|---------------|
| **Functional Code** | 18    | 214         | 33       | 2,012      | 71.1%         |
| **Tests**           | 10    | 167         | 0        | 816        | 28.9%         |
| **Total Frontend**  | 28    | 381         | 33       | 2,828      | 100%          |

**Test Coverage Ratio:** 0.41:1 (816 test lines for 2,012 functional lines)

### Frontend (Svelte)

| Category               | Files | Blank Lines | Comments | Code Lines |
|-----------------------|-------|-------------|----------|------------|
| **UI Components**      | 16    | 924         | 8        | 7,370      |

*Note: Svelte files contain integrated markup, style, and logic. No separate test files.*

### Overall Testing Summary
- **Backend:** Well-tested with 42% of code dedicated to tests
- **Frontend TypeScript:** Moderate testing with 29% test coverage
- **Frontend Svelte:** No dedicated test files (component-based testing may be integrated)

---

## 3. Code Concentration Analysis

### Backend Module Breakdown

| Module/Directory      | Files | Code Lines | % of Backend | Description                    |
|----------------------|-------|------------|--------------|--------------------------------|
| **chat/**            | 12    | 3,063      | 51.6%        | Core chat engine & adapters    |
| **tests/**           | 20    | 4,302      | 42.0%        | Test suite                     |
| **Top-level APIs**   | 7     | 1,539      | 25.9%        | Main APIs (chat, seed data)    |
| **sandbox/**         | 3     | 323        | 5.4%         | Docker sandboxing              |
| **Utilities**        | 6     | 616        | 10.4%        | DuckDB, threads, artifacts     |
| **Documentation**    | 7     | 385        | —            | Markdown docs                  |

#### Top 10 Largest Backend Files (Functional Code)

| File                                  | Lines | Description                           |
|--------------------------------------|-------|---------------------------------------|
| `chat/engine.py`                     | 1,559 | Main chat engine                      |
| `seed_data.py`                       | 287   | Data seeding functionality            |
| `tools.py`                           | 448   | Tool implementations                  |
| `chat_api.py`                        | 401   | Chat API endpoints                    |
| `seed_data_api.py`                   | 361   | Seed data API endpoints               |
| `chat/jobs.py`                       | 279   | Job management                        |
| `chat/message_builder.py`            | 233   | Message construction                  |
| `chat/tooling.py`                    | 230   | Chat tooling utilities                |
| `sandbox/docker_runner.py`           | 226   | Docker container management           |
| `chat/adapters/openai_adapter.py`    | 223   | OpenAI LLM adapter                    |

### Frontend Module Breakdown

| Module/Directory        | Files | Code Lines | % of Frontend | Description                      |
|------------------------|-------|------------|---------------|----------------------------------|
| **routes/**            | 7     | 4,121      | 40.4%         | Page components                  |
| **lib/components/**    | 10    | 3,253      | 31.9%         | Reusable UI components           |
| **lib/** (utilities)   | 26    | 2,819      | 27.6%         | TypeScript utilities & stores    |
| **styles/**            | 3     | 426        | 4.2%          | CSS styles                       |

#### Top 10 Largest Frontend Files

| File                                      | Lines | Description                        |
|------------------------------------------|-------|------------------------------------|
| `routes/chat/+page.svelte`               | 1,913 | Main chat page                     |
| `lib/components/ArtifactsPanel.svelte`   | 752   | Artifacts display panel            |
| `routes/connectors/+page.svelte`         | 695   | Connectors management page         |
| `routes/data/+page.svelte`               | 664   | Data management page               |
| `routes/worldlines/+page.svelte`         | 496   | Worldlines page                    |
| `lib/components/layout/AppLayout.svelte` | 495   | Main app layout                    |
| `lib/api/client.ts`                      | 457   | API client                         |
| `lib/components/PythonCell.svelte`       | 456   | Python code cell component         |
| `lib/components/ArtifactTablePreview.svelte` | 377 | Artifact table preview            |
| `routes/settings/+page.svelte`           | 340   | Settings page                      |

### Code Concentration Highlights

1. **Backend Core Logic:** The `chat/` directory contains 51.6% of functional backend code (3,063 lines), making it the heart of the application
2. **Single Largest File:** `chat/engine.py` at 1,559 lines is the largest functional file, containing the main chat engine logic
3. **Frontend Pages:** The `routes/` directory, particularly `chat/+page.svelte` (1,913 lines), dominates frontend code
4. **Well-Modularized:** Despite some large files, code is generally well-distributed across modules
5. **Test Coverage:** Backend has extensive tests (4,302 lines) across 20 test files

---

## 4. Repository Structure

```
analytics-agent/
├── backend/           (10,253 lines Python + docs)
│   ├── chat/          (3,063 lines - Core engine)
│   ├── tests/         (4,302 lines - Test suite)
│   ├── sandbox/       (323 lines - Docker sandbox)
│   ├── scripts/       (105 lines - Utilities)
│   └── docs/          (385 lines - Documentation)
├── frontend/          (10,639 lines TypeScript/Svelte/CSS)
│   ├── src/routes/    (4,121 lines - Pages)
│   ├── src/lib/       (6,072 lines - Components & utilities)
│   └── src/styles/    (426 lines - CSS)
└── scripts/           (32 lines - Dev scripts)
```

---

## 5. Code Quality Indicators

### Comment Density

| Language   | Comment Lines | Code Lines | Comments/Code Ratio |
|-----------|---------------|------------|---------------------|
| Python    | 1,398         | 10,253     | 13.6%               |
| TypeScript| 33            | 2,843      | 1.2%                |
| Svelte    | 8             | 7,370      | 0.1%                |
| CSS       | 60            | 426        | 14.1%               |

**Observation:** Python code has good inline documentation (13.6%), while frontend code relies more on self-documenting code patterns.

### Average File Size

| Language   | Avg Lines/File |
|-----------|----------------|
| Python    | 201            |
| Svelte    | 461            |
| TypeScript| 98             |

---

## Key Findings

1. **Balanced Full-Stack:** Nearly equal distribution between backend (42.5%) and frontend (42.4%) code
2. **Test-Driven Backend:** Strong testing culture with 42% of backend code being tests
3. **Component-Based Frontend:** Large Svelte files indicate complex, integrated components
4. **Core Concentration:** Chat engine (`chat/engine.py`) is the single most complex module
5. **Documentation Present:** 430 lines of Markdown documentation across 12 files
6. **Moderate Dependencies:** JSON files (2,702 lines) mostly package-lock.json

---

## Recommendations

1. **Consider Splitting Large Files:** `chat/engine.py` (1,559 lines) and `routes/chat/+page.svelte` (1,913 lines) could benefit from modularization
2. **Frontend Testing:** Consider adding more tests for Svelte components (currently no .test.svelte files)
3. **Documentation:** Maintain current documentation practices, potentially expand API docs
4. **Code Comments:** Frontend TypeScript/Svelte could benefit from more inline documentation

---

*Report generated using `cloc` (Count Lines of Code) version 1.98*
