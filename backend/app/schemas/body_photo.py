from datetime import datetime

from pydantic import BaseModel


class BodyPhotoResponse(BaseModel):
    body_photo_updated_at: datetime
