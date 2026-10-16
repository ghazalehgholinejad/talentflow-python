"""Typed transport envelope; action-specific business rules live in domain.py."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr


class ActionRequest(BaseModel):
    model_config = ConfigDict(extra='allow', json_schema_extra={'examples':[
        {'action':'snapshot'},
        {'action':'createJob','requestId':'example-job-1','title':'Python Engineer',
         'department':'Demo','location':'Remote','goal':'Build a demo','skills':['Python','SQL']},
    ]})
    action: Literal['snapshot', 'createJob', 'updateJob', 'syncResumes', 'importCandidate',
                    'verifyCandidate', 'runScreening', 'updateApplication', 'createInterview',
                    'getInterview', 'saveAnswer', 'finishInterview', 'questionAudio', 'recruiterReport']
    requestId: StrictStr | None = Field(default=None, min_length=1, max_length=100)
