# Analytics Agent

An intelligent analytics agent that enables natural language interaction with datasets through Python and SQL tooling. The agent provides a conversational interface for data analysis, executing queries, running Python code, and visualizing results in real-time.

## Features

- **Natural Language Analytics**: Interact with your data using conversational queries
- **Multi-Language Execution**: Supports both SQL (via DuckDB) and Python code execution
- **Sandboxed Execution**: Secure code execution in isolated Docker containers
- **Interactive Chat Interface**: Modern web UI built with SvelteKit
- **Data Visualization**: Built-in support for charts and graphs using matplotlib
- **Worldline Branching**: Create and manage multiple analysis paths (worldlines) from any point in the conversation
- **Real-time Streaming**: Server-sent events for live response updates
- **Multiple LLM Providers**: Support for OpenAI, Google Gemini, and OpenRouter

## Architecture

The project consists of two main components:

### Backend
- **FastAPI** application providing REST APIs
- **DuckDB** for SQL query execution and data storage
- **Docker** sandboxes for secure Python code execution
- **LLM Integration** for natural language understanding
- Pre-loaded with `numpy`, `pandas`, and `matplotlib`

### Frontend
- **SvelteKit** application with modern reactive UI
- **Vite** for fast development and building
- **Real-time** chat interface with code execution results
- **Data visualization** and table rendering

## Prerequisites

- **Docker**: For building and running sandboxed Python environments
- **Python 3.14+**: For the backend application
- **Node.js & npm**: For the frontend application
- **uv**: Python package manager (recommended)
- **API Key**: At least one of:
  - `OPENAI_API_KEY`
  - `GEMINI_API_KEY` (or `GOOGLE_API_KEY`)
  - `OPENROUTER_API_KEY`

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/ssh-vom/analytics-agent.git
cd analytics-agent
```

### 2. Set Up Environment Variables

Create a `.env` file in the backend directory:

```bash
# Choose one or more LLM providers
OPENAI_API_KEY=your-openai-key
# GEMINI_API_KEY=your-gemini-key
# OPENROUTER_API_KEY=your-openrouter-key

# Optional: OpenRouter configuration
# OPENROUTER_MODEL=openrouter/auto
# OPENROUTER_APP_NAME=TextQL
# OPENROUTER_HTTP_REFERER=your-app-url

# Optional: Sandbox configuration
# SANDBOX_IMAGE=textql-sandbox:py311
# SANDBOX_REAPER_INTERVAL_SECONDS=60
# SANDBOX_IDLE_TTL_SECONDS=900
```

### 3. Build and Run

Use the provided Makefile for easy setup:

```bash
# Build Docker sandbox and install dependencies
make build

# Run both backend and frontend
make run
```

Or use the development script directly:

```bash
# Full build and run
make dev

# Or manually:
./scripts/dev.sh
```

### 4. Access the Application

- **Frontend**: http://localhost:5173
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

## Getting Started

This guide will walk you through your first data analysis session with the Analytics Agent.

### First-Time Setup

1. **Verify Prerequisites**
   
   Ensure Docker is running:
   ```bash
   docker --version
   docker ps
   ```

2. **Configure Your LLM Provider**
   
   Add your API key to `backend/.env`:
   ```bash
   echo "OPENAI_API_KEY=sk-your-key-here" >> backend/.env
   ```

3. **Build the Environment**
   
   ```bash
   make build
   ```
   
   This will:
   - Build the Docker sandbox image with Python 3.11, numpy, pandas, and matplotlib
   - Install backend dependencies using `uv`
   - Install frontend dependencies using `npm`

4. **Start the Application**
   
   ```bash
   make run
   ```
   
   Wait for both services to start:
   - Backend will be available at http://localhost:8000
   - Frontend will be available at http://localhost:5173

### Your First Analysis

1. **Open the Web Interface**
   
   Navigate to http://localhost:5173 in your browser.

2. **Create a Thread**
   
   Click "New Thread" to start a conversation. Threads organize your analysis sessions.

3. **Ask a Question**
   
   Try a simple data analysis task:
   ```
   Create a sample dataset with 100 rows containing:
   - dates from the last 100 days
   - random sales values between 100 and 1000
   - product categories (A, B, C)
   
   Then show me a bar chart of average sales by category.
   ```

4. **Watch the Agent Work**
   
   The agent will:
   - Generate Python code to create the dataset
   - Execute the code in a secure Docker sandbox
   - Create visualizations using matplotlib
   - Display results and generated artifacts in the UI

5. **Explore Worldlines**
   
   At any point, you can:
   - Branch the conversation to try different approaches (click the branch icon)
   - Switch between worldlines to compare results
   - Continue from any previous point in the analysis

### Loading Your Own Data

You can load data in several ways:

1. **Using SQL (DuckDB)**
   ```
   Load data from this CSV file: /path/to/your/data.csv
   Show me the first 10 rows
   ```

2. **Using Python (pandas)**
   ```
   Read the Excel file at /path/to/data.xlsx
   Calculate summary statistics for all numeric columns
   ```

3. **Direct SQL Queries**
   ```
   CREATE TABLE sales AS 
   SELECT * FROM read_csv_auto('/path/to/sales.csv');
   
   SELECT product, SUM(revenue) as total 
   FROM sales 
   GROUP BY product 
   ORDER BY total DESC;
   ```

### Common Workflows

**Data Exploration:**
```
Load my CSV, show me the column types and missing value counts
```

**Statistical Analysis:**
```
Calculate correlation matrix for all numeric columns and create a heatmap
```

**Visualization:**
```
Create a time series plot of sales over time with a 7-day moving average
```

**Data Transformation:**
```
Create a new column called 'profit_margin' as (revenue - cost) / revenue
Then show the distribution with a histogram
```

## Development

### Backend Development

```bash
cd backend

# Install dependencies
uv sync

# Run tests
uv run pytest

# Run backend only
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend Development

```bash
cd frontend

# Install dependencies
npm install

# Run dev server
npm run dev

# Build for production
npm run build

# Run tests
npm test
```

### Building the Sandbox Image

The Python sandbox must be built before running the backend:

```bash
docker build -t textql-sandbox:py311 backend/sandbox/runner
```

This image includes:
- Python 3.11
- numpy
- pandas
- matplotlib

## Usage

### Starting a Conversation

1. Open the frontend at http://localhost:5173
2. Create a new thread or select an existing one
3. Start asking questions about your data in natural language

Example queries:
- "Load the sales data from CSV and show me the top 10 products"
- "Create a bar chart of monthly revenue"
- "Calculate the correlation between price and sales volume"

### Working with Worldlines

Worldlines allow you to branch your analysis at any point:

1. Navigate to any point in your conversation
2. Create a branch to explore alternative analyses
3. Switch between worldlines to compare different approaches

### API Endpoints

The backend provides several REST API endpoints:

- `POST /api/chat` - Send messages to the agent
- `GET /api/worldlines` - List all worldlines
- `POST /api/worldlines` - Create a new worldline
- `POST /api/tools/sql` - Execute SQL queries
- `POST /api/tools/python` - Execute Python code
- `GET /api/artifacts` - Retrieve generated artifacts

See the interactive API documentation at http://localhost:8000/docs

## Sandbox Execution

The Analytics Agent executes all Python code in isolated Docker containers to ensure security and resource control. This section explains how the sandbox system works.

### Architecture Overview

**Sandbox Manager** (`backend/sandbox/manager.py`)
- Manages lifecycle of sandbox containers per worldline
- Implements connection pooling and reuse
- Handles concurrent access with asyncio locks
- Automatically cleans up idle sandboxes

**Docker Runner** (`backend/sandbox/docker_runner.py`)
- Creates and manages Docker containers
- Configures security constraints
- Executes Python code and captures output
- Scans for generated artifacts (images, CSV files, etc.)

### Security Features

Each sandbox container runs with strict security constraints:

1. **Network Isolation**
   - `--network none`: No network access
   - Prevents external data exfiltration

2. **Capability Restrictions**
   - `--cap-drop ALL`: Drops all Linux capabilities
   - `--security-opt no-new-privileges`: Prevents privilege escalation

3. **Resource Limits**
   - **Memory**: 512MB (configurable)
   - **CPU**: 1.0 cores (configurable)
   - **Process Limit**: 128 PIDs
   - **Temp Storage**: 64MB tmpfs

4. **Filesystem Isolation**
   - `--read-only`: Container filesystem is read-only
   - `/tmp`: Only writable location (tmpfs, 64MB)
   - `/workspace`: Mounted from host for data persistence

5. **User Isolation**
   - Runs as non-root user (UID 1000)
   - Cannot modify system files

### Execution Flow

1. **Container Creation**
   ```
   User requests code execution
   ↓
   SandboxManager checks for existing container for worldline
   ↓
   If not exists, DockerRunner creates new container
   ↓
   Container starts with "sleep infinity" command
   ```

2. **Code Execution**
   ```
   Code written to /workspace/.runner_input.py
   ↓
   Execute: docker exec <sandbox_id> python /workspace/.runner_input.py
   ↓
   Capture stdout, stderr, and exit code
   ↓
   Scan workspace for new artifacts (images, CSV files, etc.)
   ↓
   Return results to user
   ↓
   Clean up .runner_input.py
   ```

3. **Container Reuse**
   - Containers persist across multiple executions within the same worldline
   - Workspace state is maintained between runs
   - Environment variables and installed packages persist
   - Reduces overhead of container creation

### Workspace Organization

Each worldline has its own workspace directory:

```
backend/data/worldlines/<worldline_id>/
├── workspace/
│   ├── artifacts/          # Generated files (images, CSVs, etc.)
│   ├── .runner_input.py    # Temporary execution script
│   └── <user files>        # Files created by Python code
└── state.duckdb            # DuckDB database for SQL queries
```

### Artifact Detection

The sandbox automatically detects and classifies generated files:

- **Images**: `.png`, `.jpg`, `.jpeg`, `.gif`, `.svg`, `.webp`
- **CSV Files**: `.csv`
- **Markdown**: `.md`
- **PDFs**: `.pdf`
- **Other Files**: Any other file type

Hidden files (starting with `.`) are excluded from artifacts.

### Timeout and Error Handling

- **Execution Timeout**: Default 30 seconds (configurable per request)
- **Timeout Behavior**: Returns partial stdout/stderr with timeout error
- **Process Errors**: Non-zero exit codes captured with stderr output
- **Container Failures**: Reported with detailed error messages

### Customizing the Sandbox

**Modifying Resource Limits**

Edit `backend/sandbox/docker_runner.py`:

```python
@dataclass(frozen=True)
class SandboxLimits:
    pids_limit: int = 256      # Increase process limit
    memory: str = "1g"         # Increase memory
    cpus: str = "2.0"          # Increase CPU allocation
```

**Adding Python Packages**

Edit `backend/sandbox/runner/Dockerfile`:

```dockerfile
RUN pip install --no-cache-dir \
    numpy==2.2.2 \
    pandas==2.2.3 \
    matplotlib==3.10.0 \
    scikit-learn==1.5.0    # Add new package
```

Then rebuild:
```bash
docker build -t textql-sandbox:py311 backend/sandbox/runner
```

**Custom Sandbox Image**

Set environment variable to use your own image:
```bash
export SANDBOX_IMAGE=my-custom-sandbox:latest
```

## Job Scheduling & Lifecycle Management

The Analytics Agent includes an automatic sandbox cleanup system to manage resources efficiently.

### Sandbox Reaper

A background task runs continuously to clean up idle sandbox containers:

**Configuration** (`backend/main.py`):

```python
REAPER_INTERVAL_SECONDS = 60    # Check for idle containers every 60 seconds
IDLE_TTL_SECONDS = 900          # Clean up containers idle for 15 minutes
```

**Environment Variables**:

```bash
# How often to check for idle sandboxes (default: 60 seconds)
SANDBOX_REAPER_INTERVAL_SECONDS=60

# How long a sandbox can be idle before cleanup (default: 900 seconds = 15 minutes)
SANDBOX_IDLE_TTL_SECONDS=900
```

### Lifecycle Stages

1. **Startup** (`app.on_event("startup")`)
   - Initialize metadata database
   - Start sandbox reaper background task
   - Reaper runs every `REAPER_INTERVAL_SECONDS`

2. **Active Use**
   - Sandboxes created on-demand per worldline
   - Each execution updates `last_used_monotonic` timestamp
   - Concurrent executions blocked by asyncio lock per sandbox

3. **Idle Detection**
   - Reaper checks all active sandboxes every interval
   - Calculates idle time: `now - last_used_monotonic`
   - Marks for cleanup if idle >= `IDLE_TTL_SECONDS` and not locked

4. **Cleanup**
   - Removes sandbox from active pool
   - Executes `docker rm -f <sandbox_id>`
   - Workspace directory persists for future use
   - Database state remains intact

5. **Shutdown** (`app.on_event("shutdown")`)
   - Stops reaper task
   - Calls `shutdown_all()` on SandboxManager
   - Removes all active containers
   - Cleans up resources gracefully

### Concurrency Model

**Per-Worldline Locking**:
- Each sandbox has an `asyncio.Lock`
- Only one code execution per sandbox at a time
- Multiple worldlines can execute in parallel
- Prevents race conditions in workspace

**Manager-Level Locking**:
- Global lock for sandbox creation/deletion
- Prevents duplicate container creation
- Thread-safe handle management
- Coordinates reaper with active executions

**Creation Coordination**:
- Uses `asyncio.Future` to coordinate multiple requests for same worldline
- First request creates container
- Subsequent requests wait for creation to complete
- All requests share the same container

### Resource Management Best Practices

1. **Adjust TTL for Your Workload**
   - Short TTL (5 min): High container churn, lower memory usage
   - Long TTL (30 min): Better performance, higher memory usage
   - Default (15 min): Balanced approach

2. **Monitoring Active Sandboxes**
   ```bash
   docker ps --filter "name=textql_"
   ```

3. **Manual Cleanup** (if needed)
   ```bash
   docker ps -a --filter "name=textql_" -q | xargs docker rm -f
   ```

4. **Resource Limits**
   - Default: 512MB RAM per sandbox
   - With 10 active worldlines: ~5GB RAM usage
   - Adjust `SandboxLimits` based on available resources

### Debugging Sandbox Issues

**View Container Logs**:
```bash
docker logs <sandbox_id>
```

**Inspect Running Container**:
```bash
docker exec -it <sandbox_id> /bin/sh
```

**Check Container Resources**:
```bash
docker stats <sandbox_id>
```

**Force Remove All Sandboxes**:
```bash
docker rm -f $(docker ps -a -q --filter "name=textql_")
```

## Project Structure

```
analytics-agent/
├── backend/
│   ├── chat/              # LLM chat engine and adapters
│   ├── sandbox/           # Docker sandbox management
│   │   └── runner/        # Sandbox Docker image
│   ├── tests/             # Backend tests
│   ├── data/              # Runtime data storage
│   ├── main.py            # FastAPI application entry point
│   ├── chat_api.py        # Chat endpoints
│   ├── tools.py           # Tool execution (SQL/Python)
│   ├── worldlines.py      # Worldline management
│   ├── duckdb_manager.py  # DuckDB operations
│   └── pyproject.toml     # Python dependencies
├── frontend/
│   ├── src/
│   │   ├── lib/           # Svelte components
│   │   ├── routes/        # SvelteKit routes
│   │   └── styles/        # Global styles
│   ├── package.json       # Node.js dependencies
│   └── vite.config.ts     # Vite configuration
├── scripts/
│   └── dev.sh             # Development startup script
├── Makefile               # Build and run commands
└── README.md              # This file
```

## Configuration

### Environment Variables

#### Backend Configuration

- `OPENAI_API_KEY` - OpenAI API key
- `GEMINI_API_KEY` / `GOOGLE_API_KEY` - Google Gemini API key
- `OPENROUTER_API_KEY` - OpenRouter API key
- `OPENROUTER_MODEL` - OpenRouter model (default: `openrouter/auto`)
- `OPENROUTER_APP_NAME` - OpenRouter app name (default: `TextQL`)
- `OPENROUTER_HTTP_REFERER` - OpenRouter HTTP referer
- `SANDBOX_IMAGE` - Docker sandbox image (default: `textql-sandbox:py311`)
- `SANDBOX_REAPER_INTERVAL_SECONDS` - Sandbox cleanup interval (default: `60`)
- `SANDBOX_IDLE_TTL_SECONDS` - Sandbox idle timeout (default: `900`)

For detailed information on customizing the sandbox environment, see the [Sandbox Execution](#sandbox-execution) section.

## Testing

### Backend Tests

```bash
cd backend
uv run pytest
```

### Frontend Tests

```bash
cd frontend
npm test
```

## Troubleshooting

### Docker Issues

If you encounter Docker-related errors:
1. Ensure Docker is running
2. Rebuild the sandbox image: `docker build -t textql-sandbox:py311 backend/sandbox/runner`
3. Check Docker permissions

### API Key Issues

If LLM requests fail:
1. Verify your API key is set in the environment
2. Check that the key has sufficient credits/quota
3. Review the backend logs for detailed error messages

### Port Conflicts

If ports 8000 or 5173 are already in use:
1. Stop the conflicting service
2. Or modify the ports in `scripts/dev.sh`

## Contributing

Contributions are welcome! Please follow these guidelines:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## License

[Add license information here]

## Acknowledgments

- Built with [FastAPI](https://fastapi.tiangolo.com/)
- Frontend powered by [SvelteKit](https://kit.svelte.dev/)
- Data processing with [DuckDB](https://duckdb.org/)
- Sandboxed execution via [Docker](https://www.docker.com/)
