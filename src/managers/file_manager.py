import os
from urllib.parse import urlparse, urlunparse, urljoin, urlsplit

from src.schemas.file_base import TreeData, TreeItem, FileItem

from src.managers.path_manager import PathManager
from src.managers.m3m8_parser import M3U8Parser
from src.managers.sys_setting import SysSetting

class FileManager():
    def __init__(self):
        pass

    @classmethod
    def GetFileItem(cls, absFile: str, absUri: str='-'):
        '''组装FileItem'''
        absUri = absUri or ""
        rltPath = PathManager.GetRltPath(absFile)          # 截取相对路径，不然太长了

        if os.path.exists(absFile):
            fileSize    = os.path.getsize(absFile)
            timeModify  = os.path.getmtime(absFile) # 返回float类型时间戳
            return True, FileItem(fileName=rltPath, fileSize=fileSize, modifyAt=timeModify, absUri=absUri)
        return False, FileItem(fileName=rltPath, fileSize="-", modifyAt="-", absUri=absUri)

    @classmethod
    def GetFileInfos(cls):
        workPath = SysSetting.GetWorkPath()
        treeData = TreeData()

        for root, dirs, files in os.walk(workPath):
            treeItem = TreeItem()
            hasItem = False
            for fil in files:
                absFile = os.path.join(root, fil)

                suffix = os.path.splitext(fil)[1].lower()
                if suffix in (".seed", ".m3u8"):
                    if suffix == ".seed" and treeItem.parent:
                        continue
                    segments = cls.GetSegments(absFile)
                    # Validate before replacing an existing task or its children.
                    if not segments:
                        continue
                    flag, fileItem = cls.GetFileItem(absFile)
                    treeItem.parent = fileItem
                    hasItem = True

                    treeItem.childs.clear()
                    treeItem.download = 0
                    for ts in segments:
                        tsAbs = PathManager.JoinPath(root, ts.name)
                        flag, fileItme = cls.GetFileItem(tsAbs, ts.absUri)
                        if flag:    # 统计下载个数
                            treeItem.download += 1
                        treeItem.childs.append(fileItme)
                elif suffix == ".mp4":
                    flag, fileItem = cls.GetFileItem(absFile)
                    treeItem.outputs.append(fileItem)
                else:
                    pass

            if hasItem:
                treeData.items.append(treeItem)
        return treeData
    
    # @classmethod
    # def GetUriByIdx(cls, absSeed: str, index: int):
    #     # 读取 m3u8 内容获取下载地址
    #     try:
    #         basePath, baseUri, content = cls.ParseSeedFile(absSeed)
    #         tsList = cls._CheckM3U8File(basePath, baseUri, content)
    #         if len(tsList) > 0:
    #             tsName = tsList[index].name
    #             absUri = tsList[index].absUri
    #             # print(f"FileManager.GetUriByIdx index:{index} basePath:{basePath}")
    #             # print(f"FileManager.GetUriByIdx index:{index} m3u8Url:{baseUri}")
    #             # print(f"FileManager.GetUriByIdx index:{index} asbUri:{absUri}")
    #             return tsName, absUri
    #     except Exception as ex:
    #         print(f"FileManager.GetUriByIdx except {str(ex)}")  
    #     return "", ""
    @classmethod
    def GetSegments(cls, absFile: str):
        """Use the same format-aware reader for scanning, downloading and merging."""
        suffix = os.path.splitext(absFile)[1].lower()
        if suffix == ".seed":
            return cls.GetSegsBySeed(absFile)
        if suffix == ".m3u8":
            return cls.GetSegsByM3U8(absFile)
        return []

    @classmethod
    def GetSegsBySeed(cls, absSeed: str):
        # 读取 m3u8 内容获取下载地址
        tsList = []
        try:
            basePath, baseUri, content = cls._ParseSeedFile(absSeed)
            return cls._CheckM3U8File(basePath, baseUri, content)
        except Exception as ex:
            print(f"FileManager.GetSegsBySeed except {str(ex)}")  
        return tsList
    
    @classmethod
    def GetSegsByM3U8(cls, absM3U8: str):
        # 读取 m3u8 内容获取下载地址
        tsList = []
        try:
            content = cls._ParseM3U8File(absM3U8)
            if not content.strip() or content.lstrip('\ufeff').strip().splitlines()[0] != "#EXTM3U":
                return []
            segments = cls._CheckM3U8File("", "", content.lstrip('\ufeff'))
            # Only recover URLs from a same-name seed; never borrow another task's URL.
            seed = os.path.splitext(absM3U8)[0] + ".seed"
            if segments and os.path.isfile(seed):
                seedSegments = cls.GetSegsBySeed(seed)
                urls = {ts.name: ts.absUri for ts in seedSegments if ts.absUri}
                for ts in segments:
                    if not ts.absUri:
                        ts.absUri = urls.get(ts.name, "")
            return segments
        except Exception as ex:
            print(f"FileManager.GetSegsByM3U8 except {str(ex)}")  
        return tsList

    @classmethod
    def CreatePlaylist(cls, absSeed: str, playDir: str, playlist: str):
        try:
            # 读取路径
            tsNames = ""
            tsList = cls.GetSegments(absSeed)
            if not tsList:
                return False
            for idx, ts in enumerate(tsList):
                if idx > 0:
                    tsNames += "\n"
                # 因为要用 ffmpeg 进程，所以指定了绝对路径
                tsName = PathManager.JoinPath(playDir, ts.name)
                tsNames += f"file '{tsName}'"

            # with open(playFile, 'wb') as f:     # 不存在则创建
            #     f.write(tsNames.encode())       # 可写入初始内容
            with open(playlist, 'w') as f:     # 不存在则创建
                f.write(tsNames)       # 可写入初始内容
            return True
        except Exception as ex:
            print(f"FileManager.GetPlaylist except {str(ex)}")
            return False

    @classmethod
    def CreateM3U8File(cls, downPath: str, absSeed: str, m3u8Name: str="download.m3u8"):
        # Completion of an existing M3U8 must not parse or overwrite it as a seed.
        if os.path.splitext(absSeed)[1].lower() == ".m3u8":
            return bool(cls.GetSegments(absSeed))
        basePath, baseUri, content = cls._ParseSeedFile(absSeed)

        # 1.检查种子内容是否合法
        tsList = cls._CheckM3U8File(basePath, baseUri, content)
        if len(tsList) <= 0:
            print(f"FileManager.CreateM3U8File CheckM3U8File Error")
            return False
        
        # 2. 确保下载文件一定存在
        PathManager.MakeDirsByPath(downPath)

        # 3. 写M3U8文件
        try:
            absSeed = PathManager.JoinPath(downPath, m3u8Name)
            with open(absSeed, 'w') as f:     # 不存在则创建
                f.write(content)       # 可写入初始内容
            return True
        except Exception as ex:
            print(f"FileManager.CreateM3U8File except:{str(ex)}")
        # return cls.AddFileInfo(filePath, seedFile)
        return False

    @classmethod
    def _CheckM3U8File(cls, basePath: str, baseUri: str, content: str):
        '''
        basePath: 下载路径
        baseUri: 下载连接地址
        content: 下载内容
        '''
        tsList = []
        try:
            parser = M3U8Parser(content=content, base_path=basePath, m3u8_uri=baseUri)
            # print("FileManager.CheckSeedFile")
            # print("FileManager.CheckSeedFile")
            tsList = parser.parse_media()
        except Exception as ex:
            print(f"FileManager._CheckM3U8File except:{str(ex)}")
        return tsList

    @classmethod
    def CreateSeedFile(cls, downPath: str, basePath: str, baseUri: str, content: str, seedName: str="download.seed"):
        # 1.检查种子内容是否合法
        tsList = cls._CheckM3U8File(basePath, baseUri, content)
        if len(tsList) <= 0:
            print(f"FileManager.CreateSeedFile CheckM3U8File Error")
            return False
        
        # 2. 确保下载文件一定存在
        PathManager.MakeDirsByPath(downPath)

        # 3. 写种子文件
        try:
            absSeed = PathManager.JoinPath(downPath, seedName)
            with open(absSeed, 'w') as f:     # 不存在则创建
                print(f"FileManager.CreateSeedFile [{basePath}]")
                print(f"FileManager.CreateSeedFile [{baseUri}]")
                f.write(basePath)
                f.write("\n")
                f.write(baseUri)
                f.write("\n")
                f.write(content)       # 可写入初始内容
            return True
        except Exception as ex:
            print(f"FileManager.CreateSeedFile except:{str(ex)}")
        # return cls.AddFileInfo(filePath, seedFile)
        return False

    @classmethod
    def _ParseSeedFile(cls, absSeed: str):
        basePath = ""
        baseUri = ""
        content = ""
        # absFile = SysSetting.GetAbsolutePath(seedFile)

        if not os.path.exists(absSeed):    # 检查文件是否存在
            print(f"文件 {absSeed} 不存在")
            return basePath, baseUri, content
        try:
            with open(absSeed, 'r') as f:     # 不存在则创建
                # basePath = f.readline().decode().strip()
                # baseUri = f.readline().decode().strip()
                # # print("basePath", basePath)
                # # print("baseUri", baseUri)
                # content = f.read().decode()
                # # print("content", content)

                basePath = f.readline().strip()
                baseUri = f.readline().strip()
                content = f.read()
        except Exception as ex:
            print(f"ParseSeedFile Error:{str(ex)}")
        return basePath, baseUri, content
    
    @classmethod
    def _ParseM3U8File(cls, absM3U8: str):
        content = ""
        # absFile = SysSetting.GetAbsolutePath(seedFile)

        if not os.path.exists(absM3U8):    # 检查文件是否存在
            print(f"文件 {absM3U8} 不存在")
            return content
        try:
            with open(absM3U8, 'r') as f:     # 不存在则创建
                content = f.read()
                # print("content", content)
        except Exception as ex:
            print(f"ParseM3U8File Error:{str(ex)}")
        return content

# 拽拽写的代码
# # vfffffffffffnngnglgnkvvkjkmkgkgk lg jkf,gvklkmgkefklmmfnknfmfnfmnmfmffknj kmg[]
# # kjjbhgdvcmnnl;aaaaaaaaaaaaaaaaaaaaaaaaa8;9;8

# fileItem = FileItem(fileName="111", fileSize=111, modifyAt="222")
# # fileItem.fileName ="111"
# # fileItem.fileSize = "11122"
# # fileItem.modifyAt = "333"
# print(fileItem)
