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
    def GetAbsolutePath(cls, fileName: str):
        if not os.path.isabs(fileName):
            workPath = cls.GetWorkPath()
            absFile = os.path.join(workPath, fileName)
            # print(absFile)
            return absFile
        return fileName
    
    @classmethod
    def GetRelativePath(cls, fileName: str):
        if os.path.isabs(fileName):
            workPath = cls.GetWorkPath()
            # return fileName.replace(workPath+os.sep, "")
            return fileName.replace(workPath, "")
        return fileName
    
    @classmethod
    def GetAbsoluteDir(cls, fileName):
        absFile = cls.GetAbsolutePath(fileName)
        return absFile.rsplit(os.sep, 1)[0]

    @classmethod
    def GetRelativeDir(cls, fileName):
        return fileName.rsplit(os.sep, 1)[0]
    
    @classmethod
    def MakeDirsByPath(cls, filePath: str):
        # 判断保存文件夹 路径是否存在。无则创建
        if not os.path.exists(filePath):
            os.makedirs(filePath)
    
    @classmethod
    def MakeDirsByFile(cls, absFile: str):
        absPath = absFile.rsplit(os.sep, 1)[0]
        # 判断保存文件夹 路径是否存在。无则创建
        if not os.path.exists(absPath):
            os.makedirs(absPath)

    @classmethod
    def IsExists(cls, absFile:str):
        '''判断文件或文件夹是否存在'''
        if not os.path.exists(absFile):
            return False
        return True

    @classmethod
    def GetPath(cls, dir, fileName):
        return os.path.join(dir, fileName)