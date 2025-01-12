# nush-mig-bot
Telegram bot for NUSH MIG.

## How to upload problems
1. Prepare pdf file of the solution of previous problem (if applicable). Name it `Problem X.pdf`.
2. Also prepare an image file of the next problem (if applicable). Name them in the format `Problem Y.jpg`, where Y = X + 1.
3. Upload these files to the Cloudflare R2 storage bucket.
4. In MongoDB, under the problems collection, insert a new document for the next problem, in the format
   ```
   _id: Leave unchanged
   problem: <problem number, as an int, ie: 69>
   answer: <problem answers, as a space-separated string, ie: "six 9 four 20">
   ```
5. When you are done, call /announcetraining or /announcepubs in the telegram channel. Note that you have to be an admin to do this. Also note that announcetraining requires you to input the number of points given for the previous question.
6. yay

## How to start a new game
1. Call /endgame in the telegram channel. Note that you have to be an admin to do this.
2. Change the game code (if applicable).
