

## 1. Clone the project

```bash
git clone https://github.com/arminak6/UpraiseProduction.git
cd UpraiseProduction
```


## 2. Add configuration

Create `.env` from the example.

Ubuntu:

```bash
cp .env.example .env
```



Edit `.env` with your database passwords and server settings.

```dotenv
UPRAISE_ENVIRONMENT=development
UPRAISE_LOCAL_TEST_MODE=false
UPRAISE_LOCAL_JWT_TEST_MODE=true
UPRAISE_JWT_TEST_CLIENT_SECRET=your-test-secret-at-least-32-characters
```


Add your `aws_credentials.json` file to the project folder.

## 3. Create the required folders


```bash
mkdir -p callback_tls course_archive
sudo chown 10001:10001 course_archive aws_credentials.json
sudo chmod 750 course_archive
sudo chmod 400 aws_credentials.json
```



## 4. Start the project

```bash
docker compose up --build -d --wait --wait-timeout 180
```

## 5. Open the project

- UI — http://127.0.0.1:8502
- Backend API documentation — http://127.0.0.1:8000/docs
- JWT instructions — [README-JWT.md](README-JWT.md)
- API inputs and outputs — [README-API.md](README-API.md)
