"""Recompute this saved experiment against the current approved labels."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'数据整理记录'))
from 重算当前人工基准评分分析 import run, metrics
if __name__=='__main__':
    run(Path(__file__).resolve().parent)
