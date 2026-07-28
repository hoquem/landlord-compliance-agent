from fastapi import FastAPI

app = FastAPI()


@app.get("/health")
async def health() -> dict:
    """Health check endpoint.

    :return: Status response.
    """
    return {"status": "ok"}
