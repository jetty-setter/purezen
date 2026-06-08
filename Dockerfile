FROM public.ecr.aws/lambda/python:3.11

COPY requirements-lambda.txt .
RUN pip install -r requirements-lambda.txt --no-cache-dir

COPY app/ ${LAMBDA_TASK_ROOT}/app/

CMD ["app.main.handler"]
