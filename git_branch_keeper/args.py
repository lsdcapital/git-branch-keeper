import argparse

def parse_args():
    parser = argparse.ArgumentParser(description="Git branch management tool")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show verbose output")
    parser.add_argument("--cleanup", action="store_true", help="Actually delete branches")
    parser.add_argument("--force", action="store_true", help="Skip confirmations")
    parser.add_argument("--stale-days", type=int, default=30, help="Days until branch is stale")
    parser.add_argument("--protected", nargs="*", default=["main", "master"], help="Protected branches")
    parser.add_argument("--ignore", nargs="*", default=[], help="Branch patterns to ignore")
    parser.add_argument("--filter", choices=["all", "stale", "merged"], default="all", help="Filter which branches to show and process (all/stale/merged)")
    parser.add_argument("--bypass-github", action="store_true", help="Skip GitHub API checks")
    parser.add_argument("--main-branch", default="main", help="Main branch name")
    
    return parser.parse_args() 