import abc

class ABCFile(metaclass=abc.ABCMeta):
    @abc.abstractmethod 
    def ParseContent(self):   #必须要定义下面的方法，如果没有就不让实例化
        pass

    @abc.abstractmethod
    def CreateContent(self):
        pass

from src.managers.m3m8_parser import M3U8Parser

class M3U8Manager(ABCFile):
    def __init__(self):
        super().__init__()

    # @classmethod
    def CheckM3U8File(cls, basePath: str, baseUri, content: str):
        tsList = []
        try:
            parser = M3U8Parser(content=content, base_path=basePath, m3u8_uri=baseUri)

            print("FileManager.CheckSeedFile")
            print("FileManager.CheckSeedFile")
            tsList = parser.parse_media()
        except Exception as ex:
            print(f"FileManager.CheckM3U8File except:{str(ex)}")
        return tsList
    
    def CreateContent(self, basePath: str, baseUri: str, content: str):
        # 1.检查种子内容是否合法
        tsList = self.CheckM3U8File(basePath, baseUri, content)
        if len(tsList) <= 0:
            return False