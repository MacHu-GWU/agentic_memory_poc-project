## Development Setup

**Package Manager:** uv (via mise)

**Core Configuration Files:**
- `mise.toml` - Project tasks and tool versions (Python 3.12, uv)
- `pyproject.toml` - Dependencies and project metadata
- `.venv/` - Virtual environment directory

**Available Tasks:**
- `mise run venv-create` - Create Python virtual environment
- `mise run venv-remove` - Remove virtual environment
- `mise run inst` - Install Python dependencies
