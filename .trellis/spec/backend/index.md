# Backend Development Guidelines

> Best practices for backend development in this project.

---

## Overview

This directory contains guidelines for backend development. Fill in each file with your project's specific conventions.

---

## Guidelines Index

| Guide | Description | Status |
|-------|-------------|--------|
| [Directory Structure](./directory-structure.md) | Module organization and file layout | To fill |
| [Database Guidelines](./database-guidelines.md) | ORM patterns, queries, migrations | To fill |
| [DB Execution](./db-execution.md) | Executor accounting, SQLite policy owner and shutdown contracts | Documented |
| [Coordination Persistence](./coordination.md) | Access, gallery jobs, SSE slots, leases and worker ownership | Documented |
| [Refactoring Compatibility](./refactoring-compatibility.md) | Facade exports, module aliases and normalization contracts | Documented |
| [HTTP Compression](./http-compression.md) | ASGI ordering, passthrough and Starlette compatibility | Documented |
| [Error Handling](./error-handling.md) | Error types, handling strategies | To fill |
| [Quality Guidelines](./quality-guidelines.md) | Code standards, forbidden patterns | To fill |
| [Logging Guidelines](./logging-guidelines.md) | Structured logging, log levels | To fill |

---

## How to Fill These Guidelines

For each guideline file:

1. Document your project's **actual conventions** (not ideals)
2. Include **code examples** from your codebase
3. List **forbidden patterns** and why
4. Add **common mistakes** your team has made

The goal is to help AI assistants and new team members understand how YOUR project works.

---

**Language**: All documentation should be written in **English**.
