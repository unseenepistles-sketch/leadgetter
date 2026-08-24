"""Dev entrypoint: `python run.py` serves the app on http://localhost:8020."""
import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8020, reload=True)
