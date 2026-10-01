Project: Code Change Impact Analyzer (project #5, Intermediate), built for your GenAI class.
Goal: Given a Git diff and a repository, find the modules, APIs, tests and docs that may be affected, retrieving context from the whole repo instead of only the changed file.
Concepts: code embeddings, RAG, LLM code analysis.
Mandatory requirements:
Firebase Authentication with Google and Apple login
AWS EC2 hosting with HTTPS, secure environment variables and public access (postponed for now)
Your decisions:
Backend: Django only (your sir's suggestion)
Built with Antigravity, with me guiding you step by step
Deployment later; build and test locally first
Short README with the important points
Where you are: You've told me you want the app to take code and tests in one input box and report where test cases fail. You also told me "Add Project" shows "Request failed", and you wanted a prompt for the Antigravity agent to fix it.
Do you want me to write that bug-fix prompt now? If so, send me the exact error from the browser console or the Django terminal, because that tells us the real cause.
