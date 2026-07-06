from datetime import date
from typing import List,Optional
from pydantic import BaseModel,field_validator,computed_field
class StudentCreate(BaseModel):
 name:str; class_name:str; section:Optional[str]=None; stream:Optional[str]=None; gender:str; admission_no:Optional[str]=None; pen_number:Optional[str]=None; father_name:Optional[str]=None; mother_name:Optional[str]=None; date_of_birth:Optional[date]=None; category:Optional[str]=None; admission_date:Optional[date]=None
 @field_validator('admission_no','pen_number','father_name','mother_name','category','section','stream',mode='before')
 @classmethod
 def empty(cls,v): return None if v=='' else v
class StudentUpdate(StudentCreate): is_active:bool=True
class StudentOut(StudentCreate):
 id:int; is_active:bool; exit_status:Optional[str]=None; exit_date:Optional[date]=None; exit_reason:Optional[str]=None
 @computed_field
 @property
 def age_as_of_september_1(self)->Optional[int]:
  if not self.date_of_birth: return None
  cutoff=date(date.today().year,9,1)
  return cutoff.year-self.date_of_birth.year-((cutoff.month,cutoff.day)<(self.date_of_birth.month,self.date_of_birth.day))
 class Config: from_attributes=True
class AttendanceItem(BaseModel): student_id:int; status:str
class AttendanceSheet(BaseModel): class_name:str; section:Optional[str]=None; attendance_date:date; records:List[AttendanceItem]
class CalendarDayCreate(BaseModel):
 start_date:date
 end_date:Optional[date]=None
 day_type:str
 description:Optional[str]=None
class LoginRequest(BaseModel): username:str; password:str
class SchoolSettingUpdate(BaseModel): school_name:str; address:str; udise_code:str
class PasswordChangeRequest(BaseModel): current_password:str; new_password:str
class UserCreateRequest(BaseModel): username:str; password:str; role:str='teacher'; school_id:Optional[int]=None; classes:List[str]=[]; section:Optional[str]=None
class SchoolCreateRequest(BaseModel): school_name:str; address:str; udise_code:str; admin_username:str; admin_password:str
class SchoolCreateConfiguredRequest(SchoolCreateRequest): classes:List[str]=[]; fields:dict={}; dashboard_groups:List[dict]=[]
class SchoolUpdateConfiguredRequest(BaseModel): school_name:str; address:str; udise_code:str; admin_username:str; admin_password:Optional[str]=None; classes:List[str]=[]; fields:dict={}; dashboard_groups:List[dict]=[]
class AcademicYearCreate(BaseModel): name:str; start_date:date; end_date:date; is_active:bool=False
class PromotionRequest(BaseModel): from_academic_year_id:int; to_academic_year_id:int; promotions:List[dict]
class StudentExitRequest(BaseModel): exit_status:str; exit_date:date; exit_reason:Optional[str]=None
class ResetPasswordRequest(BaseModel): new_password:str
