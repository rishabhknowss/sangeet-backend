from flask import Flask, request, jsonify, send_file
from apscheduler.schedulers.background import BackgroundScheduler
import replicate
import os
from dotenv import load_dotenv
from deepface import DeepFace
from werkzeug.utils import secure_filename
import requests
from pydub import AudioSegment
import io
import time
import random
from flask_cors import CORS
import boto3
import openai
from OpenSSL import SSL
from pymongo.mongo_client import MongoClient
from pymongo.server_api import ServerApi
from datetime import datetime, timedelta

scheduler = BackgroundScheduler()

system_prompt = "Given a music prompt describing the mood, theme, and style of a song or album, generate an image prompt that represents the album cover for this music. The image should capture the essence of the music, its emotions, and the overall vibe it conveys. Be creative and imaginative in your image prompt generation.[prompt should be only in 25 words] prompt:"
music_system_prompt = "enhance this prompt for a music generation AI model [in 25 words] prompt:"


load_dotenv()
REPLICATE_API_TOKEN = os.getenv('REPLICATE_API_TOKEN')
username = 'rishabhrai1515'
password = 'eJrCJkoXGojWiKr9'
uri = "mongodb+srv://"+username+":"+password+"@cluster0.nbrmw2n.mongodb.net/"
openai.api_key = os.getenv('OPENAI_KEY')
client = MongoClient(uri, server_api=ServerApi('1'))


def generate_filename(file_type, name=""):
    current_time = int(time.time())
    rand_num = random.randint(1000,9999)
    if file_type == "audio":
        filename = str(current_time) + "_" + str(rand_num) + ".wav"
    elif file_type == "image":
        file_ext = name.split('.')[-1]
        filename = str(current_time) + "_" + str(rand_num) + "." + file_ext
    return filename

def upload_file( file_path):
    s3 = boto3.client('s3')
    bucket_name = 'sangeet'
    object_key = file_path

    try:
        s3.upload_file(file_path, bucket_name, object_key)
        print(f"File '{file_path}' uploaded to '{bucket_name}' as '{object_key}'")
    except Exception as e:
        print(f"An error occurred: {str(e)}")
    

    presigned_url = s3.generate_presigned_url(
        'get_object',
        Params={'Bucket': bucket_name, 'Key': object_key},
        ExpiresIn=604800
    )

    return presigned_url


def audio_continuation(song_link, count):
    audio_files_links = []
    audio_files_links.append(song_link)
    for i in range(count):
        contination_params = {"model_version": "melody", "input_audio": song_link, "continuation": True, "duration": 10, "continuation_start": 7, "continuation_end": 10}
        song_link = replicate.run("meta/musicgen:7a76a8258b23fae65c5a22debb8841d1d7e816b75c2f24218cd2bd8573787906",input=contination_params)
        audio_files_links.append(song_link)
        print(song_link)
    return audio_files_links

def combine_audio_files(files_list):
    audio_file_name = generate_filename("audio")
    combined_audio = AudioSegment.empty()
    for link in files_list:
        try:
            response = requests.get(link)
            if response.status_code == 200:
                audio_segment = AudioSegment.from_wav(io.BytesIO(response.content))
                combined_audio += audio_segment
            else:
                print(f"Failed to download {link}. Status code: {response.status_code}")
        except Exception as e:
            print(f"Error while processing {link}: {str(e)}")
    file_path = "audio/{}".format(audio_file_name)
    combined_audio.export(file_path, format="wav")
    s3_link = upload_file(file_path)
    return s3_link

app = Flask(__name__)
CORS(app)

# scheduler = BackgroundScheduler()
# cert_file = '/home/ec2-user/certs/sangeet.cert'
# key_file = '/home/ec2-user/certs/sangeet.key'

@app.route('/api/data/song')
def fetch_full_song():
    try:
        count = 1
        prompt = request.args.get('prompt')
        prompt_arr = prompt.split()
        if len(prompt_arr) <= 3:
            music_chat_completion = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[
            {
                "role": "user",
                "content": music_system_prompt+prompt
            }
            ]);
            params = {"model_version": "melody", "prompt": music_chat_completion["choices"][0]["message"]["content"], "duration": 30}
        else:
            params = {"model_version": "melody", "prompt": prompt, "duration": 30}
        audio_files_links = []
        song_link = replicate.run(
        "meta/musicgen:7a76a8258b23fae65c5a22debb8841d1d7e816b75c2f24218cd2bd8573787906",
        input=params)
        # audio_files_links = audio_continuation(song_link, count)
        audio_files_links.append(song_link)
        combined_file_path = combine_audio_files(audio_files_links)
        chat_completion = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[
        {
            "role": "user",
            "content": system_prompt+prompt
        }
        ]);
        print(chat_completion["choices"][0]["message"]["content"])
        output = replicate.run(
        "stability-ai/sdxl:2b017d9b67edd2ee1401238df49d75da53c523f36e363881e057f5dc3ed3c5b2",
        input={"prompt": chat_completion["choices"][0]["message"]["content"]},
        )
        response_obj = {"songUrl": combined_file_path, "coverUrl": output[0], "title": prompt, "img_prompt": chat_completion["choices"][0]["message"]["content"]}
        mongo_db = client["REQUESTS"]
        mongo_collection = mongo_db["song_requests"]
        mongo_collection.insert_one({"songUrl": combined_file_path, "coverUrl": output[0], "title": prompt, "img_prompt": chat_completion["choices"][0]["message"]["content"], "created_at":datetime.utcnow()})
        return jsonify(response_obj)
    except Exception as e:
        response_obj = {"error": str(e)}
        return jsonify(response_obj)

@app.route('/api/data/detect_emotion', methods=("POST", "GET"))
def fetch_song_from_emotion():
    try:
        uploaded_img = request.files['uploaded-img']
        print(uploaded_img)
        img_filename = secure_filename(uploaded_img.filename)
        img_filename = generate_filename("image",img_filename)
        img_path = "image/{}".format(img_filename)
        uploaded_img.save(img_path)
        result = DeepFace.analyze(img_path, actions=["emotion"], enforce_detection=False)
        args = request.form
        args = args.to_dict()
        emotion = result[0]["dominant_emotion"]
        prompt = "Generate a piece of music that conveys the emotion of {}, with a {} mood, {} tempo, in the {} genre".format(emotion, args['mood'], args['tempo'], args['genre'])
        params = {"model_version": "melody", "prompt": prompt, "duration": 30}
        audio_files_links = []
        song_link = replicate.run(
        "meta/musicgen:7a76a8258b23fae65c5a22debb8841d1d7e816b75c2f24218cd2bd8573787906",
        input=params)
        # audio_files_links = audio_continuation(song_link, 1)
        audio_files_links.append(song_link)
        combined_file_path = combine_audio_files(audio_files_links)
        chat_completion = openai.ChatCompletion.create(model="gpt-3.5-turbo", messages=[
        {
            "role": "user",
            "content": system_prompt+prompt
        }
        ]);
        print(chat_completion["choices"][0]["message"]["content"])
        output = replicate.run(
        "stability-ai/sdxl:2b017d9b67edd2ee1401238df49d75da53c523f36e363881e057f5dc3ed3c5b2",
        input={"prompt": chat_completion["choices"][0]["message"]["content"]},
        )
        # return jsonify(chat_completion,output)
        response_obj = {"songUrl": combined_file_path, "coverUrl": output[0], "title": prompt, "img_prompt": chat_completion["choices"][0]["message"]["content"]}
        mongo_db = client["REQUESTS"]
        mongo_collection = mongo_db["song_requests"]
        mongo_collection.insert_one({"songUrl": combined_file_path, "coverUrl": output[0], "title": prompt, "img_prompt": chat_completion["choices"][0]["message"]["content"], "created_at":datetime.utcnow()})
        return jsonify(response_obj)
    except Exception as e:

        response_obj = {"error": str(e)}
        return jsonify(response_obj)

@app.route('/api/data/all_songs')
def fetch_all_songs():
    try:
        mongo_db = client["REQUESTS"]
        mongo_collection = mongo_db["song_requests"]
        one_week_ago = datetime.utcnow() - timedelta(days=7)
        query = {"created_at": {"$gte": one_week_ago, "$lt": datetime.utcnow()}}
        query_data = mongo_collection.find(query, {"_id":0})
        query_data = list(query_data)
        response_obj = {"all_songs": query_data}
        return jsonify(response_obj)
    except Exception as e:
        response_obj = {"error": str(e)}
        return jsonify(response_obj)
    
@app.route('/api/data/random_song')
def fetch_random_song():
    try:
        mongo_db = client["REQUESTS"]
        mongo_collection = mongo_db["song_requests"]
        pipeline = [{"$sample": {"size": 1}},{"$project": {"_id": 0}}]
        query_data = list(mongo_collection.aggregate(pipeline))
        response_obj = {"random_song": query_data}
        return jsonify(response_obj)
    except Exception as e:
        response_obj = {"error": str(e)}
        return jsonify(response_obj)


@app.route('/api/delete')
def delete_old_files():
    remove_old_files("audio")
    remove_old_files("image")
    return jsonify("files deleted")

@app.route('/')
def home():
    obj = {"songUrl": "songlinkdsjnkjd", "coverUrl": "coverliaindjnk"}
    return jsonify(obj)

def remove_old_files():
    curr_time = time.time()
    files = os.listdir("image")
    print(files)

    for file in files:
        file_time = file.split("_")[0]
        if curr_time - int(file_time) > 1800:
            os.remove("image/" + file)
    
    files = os.listdir("audio")
    print(files)
    for file in files:
        file_time = file.split("_")[0]
        if curr_time - int(file_time) > 1800:
            os.remove("audio/" + file)

scheduler.add_job(remove_old_files, 'interval', minutes=25)

if __name__ == '__main__':
    print(os.getcwd())
    #scheduler.start()
    #context = (cert_file, key_file)
    app.run()