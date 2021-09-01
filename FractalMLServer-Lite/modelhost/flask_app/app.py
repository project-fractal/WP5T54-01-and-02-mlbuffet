import os
from os import getcwd, path
from os import listdir

import random

import onnx
import onnxruntime as rt

from flask import Flask, request, Response
from flask_httpauth import HTTPTokenAuth
from werkzeug.exceptions import HTTPException, Unauthorized

import cv2

from utils import metric_manager
from utils.container_logger import Logger
from utils.modelhost_pojos import HttpJsonResponse, Prediction, ModelList, ModelInformation

# TODO: more prometheus metrics
# TODO: (endpoint for each modelhost prometheus metrics)
# TODO: comments
# TODO: reorder methods
# TODO: where to update model list
# TODO: rethink routes

# Path constants
API_BASE_URL = '/api/v1/'
MODELHOST_BASE_URL = '/modelhost'
CACHE_FOLDER = '/root/.cache'
MODEL_FOLDER = path.join(getcwd(), 'models')

# Authorization constants
auth_token = 'password'  # TODO: https://github.com/miguelgrinberg/Flask-HTTPAuth/blob/main/examples/token_auth.py
auth = HTTPTokenAuth('Bearer')
auth.auth_error_callback = lambda *args, **kwargs: handle_exception(Unauthorized())

# Unique ID for modelhost instances
MODELHOST_NODE_UNIQ_ID = '{:06d}'.format(random.randint(1, 99999))

# Logger initialization
logger = Logger('modelhost').get_logger('modelhost')
logger.info('Starting Flask API...')

# Server initialization
server = Flask(__name__)
logger.info('... Flask API succesfully started')


# All the methods supported by the API are described below
# These methods are not supposed to be exposed to the user, who should communicate
# with Inferrer instead. These methods shall be called by Inferrer


# Call this function every time a model is added, modified or deleted
def update_model_sessions():
    model_sessions.clear()

    for model_name in listdir(MODEL_FOLDER):
        model_path = path.join(MODEL_FOLDER, model_name)
        model = onnx.load(model_path)

        # Get model metadata
        inference_session = rt.InferenceSession(model_path)
        model_type = model.graph.node[0].name
        num_inputs = inference_session.get_inputs()[0].shape  # TODO dimensions
        input_name = inference_session.get_inputs()[0].name
        output_name = inference_session.get_outputs()[0].name
        label_name = inference_session.get_outputs()[0].name
        description = model.doc_string

        full_description = {'model': model,
                            'inference_session': inference_session,
                            'model_type': model_type,
                            'num_inputs': num_inputs,
                            'input_name': input_name,  # TODO: or input name?
                            'output_name': output_name,
                            'label_name': label_name,
                            'description': description}

        model_sessions[model_name] = full_description


@auth.verify_token
def verify_token(token):
    return token == auth_token


@server.route(MODELHOST_BASE_URL, methods=['GET'])
def hello_world():
    return HttpJsonResponse(
        200,
        http_status_description='Greetings from Fractal - ML Server - ModelHost, the Machine Learning model server. '
                                'Are you supposed to be reading this? Guess not. Go to Inferrer!'
    ).json()


@server.route(path.join(MODELHOST_BASE_URL, 'api/test'), methods=['GET'])
def get_test():
    metric_manager.increment_test_counter()
    return HttpJsonResponse(200).json()


@server.route(path.join(MODELHOST_BASE_URL, 'api/test/<data>'), methods=['GET'])
def get_test_data(data):
    print(f'Received data: "{data}"')
    return HttpJsonResponse(
        200,
        http_status_description=f'Received "{data}" from modelhost {MODELHOST_NODE_UNIQ_ID}'
    ).json()


@server.route('/metrics', methods=['GET', 'POST'])
def get_metrics():  # TODO: where is the result of this method used
    # force refresh system metrics
    metric_manager.compute_system_metrics()
    metrics = metric_manager.get_metrics()
    return Response(
        metrics, mimetype='text/plain'
    )


@server.errorhandler(HTTPException)
def handle_exception(exception):
    """Return JSON instead of HTML for HTTP errors."""

    return HttpJsonResponse(
        http_status_code=exception.code,
        http_status_name=exception.name,
        http_status_description=exception.description
    ).json()


@server.before_request
def log_call():
    if request.path == '/metrics':  # don't log prometheus' method
        pass
    else:
        client_ip = request.environ.get('HTTP_X_REAL_IP', request.remote_addr)
        logger.info(f'[{client_ip}] HTTP {request.method} call to {request.path}')


@server.after_request
def log_response(response):
    if request.path == '/metrics':  # don't log prometheus' method
        pass
    elif request.path == '/help':  # don't display the whole help
        logger.info('Help displayed')
    elif request.path == '/modelhost/models':
        logger.info('Models list provided')
    elif request.path == '/modelhost/models/information':
        logger.info('Models & description list provided')
    elif 'image' in request.files:
        logger.info('Prediction done')
    elif response:
        logger.info(response.get_json())

    return response


@server.route(path.join(MODELHOST_BASE_URL, 'models/<model_name>/prediction'), methods=['POST', 'PUT'])
def predict(model_name):
    metric_manager.increment_model_counter()

    # Check that the model exists
    if model_name not in model_sessions.keys():
        return Prediction(
            404,
            http_status_description=f'{model_name} does not exist. '
                                    f'Visit GET {path.join(API_BASE_URL, "models")} for a list of avaliable models'
        ).json()

    inference_session = model_sessions[model_name]['inference_session']
    input_name = model_sessions[model_name]['input_name']
    output_name = model_sessions[model_name]['output_name']
    if request.method == 'POST':
        new_observation = request.json['values']

        try:
            prediction = inference_session.run(
                [output_name],
                {input_name: [new_observation]}
            )[0]

        # Error provided by the model
        except Exception as error:
            return Prediction(
                500, http_status_description=str(error)
            ).json()

        # Correct prediction
        return Prediction(
            200, http_status_description='Prediction successful', values=prediction
        ).json()

    elif request.method == 'PUT':
        new_observation = request.files['image']
        filename = request.form['filename']
        image_path = path.join(CACHE_FOLDER, filename)
        new_observation.save(image_path)
        img = cv2.imread(image_path)
        os.remove(image_path)
        try:
            prediction = inference_session.run(
                [output_name],
                {input_name: [img]}
            )[0]

        # Error provided by the model
        except Exception as error:
            return Prediction(
                500, http_status_description=str(error)
            ).json()

        # Correct prediction
        return Prediction(
            200, http_status_description='Prediction successful', values=prediction
        ).json()


@server.route(path.join(MODELHOST_BASE_URL, '<model_name>/information'), methods=['GET', 'POST'])
def model_information(model_name):
    if request.method == 'GET':
        # Check that the model exists
        if model_name not in model_sessions.keys():
            return ModelInformation(
                404,
                http_status_description=f'{model_name} does not exist. '
                                        f'Visit GET {path.join(API_BASE_URL, "models")} for a list of avaliable models'
            ).json()

        description = model_sessions[model_name]
        return ModelInformation(
            200,
            input_name=description['input_name'],
            num_inputs=description['num_inputs'],
            output_name=description['output_name'],
            description=description['description'],
            model_type=description['model_type']).json()

    elif request.method == 'POST':
        # Check that the model exists
        if model_name not in model_sessions.keys():
            return HttpJsonResponse(
                404,
                http_status_description=f'{model_name} does not exist. '
                                        f'Visit GET {path.join(API_BASE_URL, "models")} for a list of avaliable models'
            ).json()

        model_path = path.join(MODEL_FOLDER, model_name)
        new_model_description = request.json['model_description']

        model_sessions[model_name]['description'] = new_model_description
        model = onnx.load(model_path)
        model.doc_string = new_model_description
        onnx.save(model, model_path)
        return HttpJsonResponse(200).json()


@server.route(path.join(MODELHOST_BASE_URL, 'models'), methods=['GET'])
def get_model_list():
    return ModelList(
        200, model_list=list(model_sessions.keys())
    ).json()


@server.route(path.join(MODELHOST_BASE_URL, 'models/information'), methods=['GET'])
def get_model_list_information():
    # update_model_sessions()  # TODO where
    # TODO: WORKAROUND, THIS MAKES NO SENSE
    model_name = list(model_sessions.keys())[0]
    description = model_sessions[model_name]

    return ModelInformation(
        200,
        input_name=description['input_name'],
        num_inputs=description['num_inputs'],
        output_name=description['output_name'],
        description=description['description'],
        model_type=description['model_type']
    ).json()


@server.route(path.join(MODELHOST_BASE_URL, 'models/<model_name>'), methods=['PUT', 'DELETE'])
def manage_model(model_name):
    model_path = path.join(MODEL_FOLDER, model_name)

    if request.method == 'PUT':
        # get model and save it
        model = request.files['model']
        model.save(model_path)
        return HttpJsonResponse(201).json()

    elif request.method == 'DELETE':
        # if the model exists
        if os.path.isfile(model_path):
            os.remove(model_path)
            return HttpJsonResponse(204).json()
        else:
            return HttpJsonResponse(
                404,
                http_status_description=f'{model_name} does not exist. Visit GET {path.join(API_BASE_URL, "models")} '
                                        f'for a list of avaliable models'
            ).json()


@server.route(path.join(MODELHOST_BASE_URL, 'models/update'), methods=['POST'])
def update_models():
    update_model_sessions()
    return HttpJsonResponse(200).json()


# List with preloaded models to do the inference
model_sessions = {}
update_model_sessions()

if __name__ == '__main__':
    pass
