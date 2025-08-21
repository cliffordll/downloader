import os

class SysSetting(object):
    def __init__(self):
        pass

    @classmethod
    def GetWorkPath(cls):
        # return os.path.join(os.getcwd(), f"downloads{os.sep}videos")
        return os.path.join(os.getcwd(), f"downloads{os.sep}")
    
    @classmethod
    def GetTimeout(cls):
        return 10
    
    @classmethod
    def GetMaxWorkers(cls):
        return 3