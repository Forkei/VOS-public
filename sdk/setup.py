from setuptools import setup, find_packages

setup(
    name="vos-sdk",
    version="0.1.1",
    description="Virtual Operating System SDK for standardized agent communication",
    author="VOS Development Team",
    author_email="dev@vos.ai",
    packages=find_packages(),
    install_requires=[
        "httpx>=0.24.0",
        "pydantic>=2.0.0",
        "pika>=1.3.0",  # RabbitMQ client (sync)
        "aio-pika>=9.0.0",  # RabbitMQ client (async)
        "psycopg2-binary>=2.9.0",  # PostgreSQL client
        "weaviate-client>=3.0.0",  # Weaviate vector store
        "google-genai>=0.1.0",  # New Google GenAI SDK
        "python-dotenv>=1.0.0",  # .env file support
    ],
    python_requires=">=3.8",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
    ],
)