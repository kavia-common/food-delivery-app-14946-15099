#!/bin/bash
cd /home/kavia/workspace/code-generation/food-delivery-app-14946-15099/OrderService
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

