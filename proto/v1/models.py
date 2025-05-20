from typing import List, Dict, Optional, Any
from pydantic import BaseModel
from crate.api.types import AnswerData
from crate.api.types import ChatData
from crate.api.types import HelloData
from crate.api.types import UserChangePasswordRequest as UserChangePassReq

ComplexAliasType = Dict[str, int]