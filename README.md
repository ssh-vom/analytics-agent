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

### Customizing the Sandbox

To customize the Python sandbox environment, modify the Dockerfile at `backend/sandbox/runner/Dockerfile` and rebuild:

```bash
docker build -t textql-sandbox:py311 backend/sandbox/runner
```

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
