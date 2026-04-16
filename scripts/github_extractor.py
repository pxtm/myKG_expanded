"""
GitHub repository data extractor.
Fetches repositories and their metadata from GitHub API.
"""

from github import Github, GithubException
from typing import List, Dict, Optional
import logging
from datetime import datetime
import json
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class GitHubExtractor:
    """Extract data from GitHub repositories."""
    
    def __init__(self, token: str, username: Optional[str] = None):
        """Initialize GitHub API client."""
        self.github = Github(token)
        self.username = username
        logger.info(f"GitHub API initialized for user: {username or 'token-based'}")
    
    def get_user_repos(self, username: Optional[str] = None, 
                       max_repos: int = 50, include_forks: bool = False) -> List[Dict]:
        """Fetch all repositories for a user."""
        username = username or self.username
        if not username:
            raise ValueError("Username must be provided")
        
        try:
            user = self.github.get_user(username)
            repos = []
            
            for repo in user.get_repos():
                if len(repos) >= max_repos:
                    break
                
                if not include_forks and repo.fork:
                    continue
                
                repo_data = self.extract_repo_data(repo)
                repos.append(repo_data)
                logger.info(f"Extracted: {repo.full_name}")
            
            logger.info(f"Extracted {len(repos)} repositories")
            return repos
            
        except GithubException as e:
            logger.error(f"GitHub API error: {e}")
            return []
    
    def extract_repo_data(self, repo) -> Dict:
        """Extract relevant data from a repository object."""
        # Get topics/tags
        topics = repo.get_topics() if hasattr(repo, 'get_topics') else []
        
        # Get primary language and all languages
        languages = {}
        try:
            languages = repo.get_languages()
        except:
            pass
        
        # Get README content
        readme_content = ""
        try:
            readme = repo.get_readme()
            readme_content = readme.decoded_content.decode('utf-8')
        except:
            pass
        
        return {
            'id': repo.full_name,
            'name': repo.name,
            'description': repo.description or "",
            'language': repo.language or "Unknown",
            'languages': list(languages.keys()),
            'topics': topics,
            'stars': repo.stargazers_count,
            'forks': repo.forks_count,
            'created_at': repo.created_at.isoformat(),
            'updated_at': repo.updated_at.isoformat(),
            'url': repo.html_url,
            'readme': readme_content[:5000],  # Limit README length
            'is_fork': repo.fork,
            'default_branch': repo.default_branch,
        }
    
    def get_repo_dependencies(self, repo_name: str) -> List[str]:
        """Extract dependencies from a repository."""
        dependencies = []
        
        try:
            repo = self.github.get_repo(repo_name)
            
            # Check for package.json (Node.js)
            try:
                package_json = repo.get_contents("package.json")
                content = json.loads(package_json.decoded_content)
                deps = content.get('dependencies', {})
                dev_deps = content.get('devDependencies', {})
                dependencies.extend(list(deps.keys()) + list(dev_deps.keys()))
            except:
                pass
            
            # Check for requirements.txt (Python)
            try:
                requirements = repo.get_contents("requirements.txt")
                content = requirements.decoded_content.decode('utf-8')
                for line in content.split('\n'):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        dep = line.split('==')[0].split('>=')[0].split('<=')[0]
                        dependencies.append(dep)
            except:
                pass
            
            # Check for go.mod (Go)
            try:
                go_mod = repo.get_contents("go.mod")
                content = go_mod.decoded_content.decode('utf-8')
                for line in content.split('\n'):
                    if line.strip().startswith('require'):
                        parts = line.split()
                        if len(parts) >= 2:
                            dependencies.append(parts[1])
            except:
                pass
            
        except Exception as e:
            logger.warning(f"Could not extract dependencies from {repo_name}: {e}")
        
        return dependencies
    
    def search_repos_by_topic(self, topic: str, max_results: int = 20) -> List[Dict]:
        """Search for repositories by topic."""
        try:
            query = f"topic:{topic}"
            repos = self.github.search_repositories(query=query)
            
            results = []
            for repo in repos[:max_results]:
                results.append(self.extract_repo_data(repo))
            
            logger.info(f"Found {len(results)} repos for topic: {topic}")
            return results
            
        except GithubException as e:
            logger.error(f"Search error: {e}")
            return []
    
    def export_to_json(self, repos: List[Dict], output_path: str):
        """Export repository data to JSON file."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_file, 'w') as f:
            json.dump(repos, f, indent=2)
        
        logger.info(f"Exported {len(repos)} repositories to {output_path}")


if __name__ == "__main__":
    # Example usage
    from dotenv import load_dotenv
    import os
    
    load_dotenv(override=True)

    token = os.getenv("GITHUB_TOKEN")
    username = os.getenv("GITHUB_USERNAME")
    
    if not token:
        logger.error("GITHUB_TOKEN not found in environment")
    else:
        extractor = GitHubExtractor(token, username)
        repos = extractor.get_user_repos(max_repos=10)
        
        if repos:
            extractor.export_to_json(repos, "data/github_repos.json")
            print(f"\nExtracted {len(repos)} repositories")
            print("\nSample repo:")
            print(json.dumps(repos[0], indent=2)[:500])
