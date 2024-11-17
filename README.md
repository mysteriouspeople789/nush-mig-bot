# nush-mig-bot
Telegram bot for NUSH MIG.

## How to upload problems
1. Prepare pdf file of the solution of previous problem (if applicable), and image file of the next problem (if applicable). Name them in the format ```Problem X.pdf/jpg/png```.
2. Upload these files to the Cloudflare R2 storage bucket.
3. In MongoDB, under the problems collection, insert a new document for the next problem, in the format
   ```_id: Leave unchanged``
   problem: <problem number, as an int>``
   answer: <problem answer, as a string>```
4. yay

Note: Ensure this is done before the next problem is scheduled to be released.
